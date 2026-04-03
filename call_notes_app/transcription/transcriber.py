import asyncio
import threading
import numpy as np
import sounddevice as sd
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
from config import SAMPLE_RATE, CHANNELS, AWS_REGION


class _TranscriptHandler(TranscriptResultStreamHandler):
    """Handles streaming transcript events from Amazon Transcribe."""

    # Channel ID mapping: ch_0 = system audio (customer), ch_1 = mic (you)
    _CHANNEL_LABELS = {"ch_0": "Customer", "ch_1": "You"}

    def __init__(self, stream, on_partial, on_final, dual_channel=False):
        super().__init__(stream)
        self.on_partial = on_partial
        self.on_final = on_final
        self.full_transcript = []
        self._last_speaker = None
        self._dual_channel = dual_channel

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            text = alt.transcript.strip()
            if not text:
                continue

            # Determine speaker label
            speaker = None
            if self._dual_channel and hasattr(result, "channel_id") and result.channel_id:
                speaker = self._CHANNEL_LABELS.get(result.channel_id, result.channel_id)
            elif alt.items:
                for item in alt.items:
                    if item.speaker:
                        speaker = item.speaker
                        break

            if result.is_partial:
                display = self._format_line(speaker, text)
                if self.on_partial:
                    self.on_partial(display)
            else:
                display = self._format_line(speaker, text)
                self.full_transcript.append(display)
                if self.on_final:
                    self.on_final(display)
                if speaker:
                    self._last_speaker = speaker

    def _format_line(self, speaker, text):
        """Prefix text with speaker label if available."""
        if speaker:
            return f"[{speaker}]: {text}"
        return text



class LiveTranscriber:
    """Captures system audio + mic and streams to Amazon Transcribe."""

    def __init__(self, system_device=None, mic_device=None, on_partial=None, on_final=None):
        self.system_device = system_device
        self.mic_device = mic_device
        self.on_partial = on_partial
        self.on_final = on_final
        self._running = False
        self._system_stream = None
        self._mic_stream = None
        self._thread = None
        self._handler = None
        self.on_status = None  # Optional callback for connection status messages

        # Reconnection settings
        self._max_reconnect_attempts = 5
        self._reconnect_delay_base = 2  # seconds, doubles each attempt

        # Separate buffers for mixing
        self._lock = threading.Lock()
        self._system_buffer = np.empty((0,), dtype=np.float32)
        self._mic_buffer = np.empty((0,), dtype=np.float32)

    def _system_callback(self, indata, frames, time_info, status):
        if status:
            print(f"System audio status: {status}")
        try:
            with self._lock:
                self._system_buffer = np.concatenate(
                    [self._system_buffer, indata[:, 0].copy()]
                )
        except Exception as e:
            print(f"System audio callback error: {e}")

    def _mic_callback(self, indata, frames, time_info, status):
        if status:
            print(f"Mic audio status: {status}")
        try:
            with self._lock:
                self._mic_buffer = np.concatenate(
                    [self._mic_buffer, indata[:, 0].copy()]
                )
        except Exception as e:
            print(f"Mic audio callback error: {e}")

    def _is_dual_channel(self):
        """True when both system and mic devices are active."""
        return self.system_device is not None and self.mic_device is not None

    def _get_audio_chunk(self, num_samples):
        """Return PCM16 bytes for Transcribe, or None if not enough data.

        Dual-channel (both devices): interleaved stereo — ch_0 = system, ch_1 = mic.
        Single device: mono PCM16.
        """
        has_system = self.system_device is not None
        has_mic = self.mic_device is not None

        with self._lock:
            if has_system and has_mic:
                ready = min(len(self._system_buffer), len(self._mic_buffer))
            elif has_system:
                ready = len(self._system_buffer)
            else:
                ready = len(self._mic_buffer)

            if ready < num_samples:
                return None

            if has_system and has_mic:
                sys_chunk = self._system_buffer[:num_samples]
                mic_chunk = self._mic_buffer[:num_samples]
                self._system_buffer = self._system_buffer[num_samples:]
                self._mic_buffer = self._mic_buffer[num_samples:]

                # Clip each channel independently
                sys_chunk = np.clip(sys_chunk, -1.0, 1.0)
                mic_chunk = np.clip(mic_chunk, -1.0, 1.0)

                # Interleave as stereo: [sys0, mic0, sys1, mic1, ...]
                stereo = np.empty(num_samples * 2, dtype=np.float32)
                stereo[0::2] = sys_chunk
                stereo[1::2] = mic_chunk
                pcm16 = (stereo * 32767).astype(np.int16)
                return pcm16.tobytes()
            elif has_system:
                mono = self._system_buffer[:num_samples]
                self._system_buffer = self._system_buffer[num_samples:]
            else:
                mono = self._mic_buffer[:num_samples]
                self._mic_buffer = self._mic_buffer[num_samples:]

        # Single-channel path
        peak = np.max(np.abs(mono))
        if peak > 1.0:
            mono = mono / peak
        pcm16 = (mono * 32767).astype(np.int16)
        return pcm16.tobytes()

    async def _stream_audio(self, transcribe_stream):
        """Feed audio chunks to Amazon Transcribe."""
        # Send ~100ms chunks (1600 samples at 16kHz)
        chunk_samples = SAMPLE_RATE // 10

        while self._running:
            audio_bytes = self._get_audio_chunk(chunk_samples)
            if audio_bytes:
                await transcribe_stream.input_stream.send_audio_event(
                    audio_chunk=audio_bytes
                )
            else:
                await asyncio.sleep(0.05)

        await transcribe_stream.input_stream.end_stream()

    async def _run_transcription(self):
        """Main async loop: connect to Transcribe and stream audio."""
        client = TranscribeStreamingClient(region=AWS_REGION)
        dual = self._is_dual_channel()

        if dual:
            # Dual-channel: each device gets its own channel, Transcribe
            # transcribes them independently.  Cannot combine with
            # show_speaker_label (API constraint).
            stream = await client.start_stream_transcription(
                language_code="en-US",
                media_sample_rate_hz=SAMPLE_RATE,
                media_encoding="pcm",
                enable_channel_identification=True,
                number_of_channels=2,
                enable_partial_results_stabilization=True,
                partial_results_stability="high",
            )
        else:
            # Single device — fall back to speaker diarization
            stream = await client.start_stream_transcription(
                language_code="en-US",
                media_sample_rate_hz=SAMPLE_RATE,
                media_encoding="pcm",
                show_speaker_label=True,
                enable_partial_results_stabilization=True,
                partial_results_stability="high",
            )

        self._handler = _TranscriptHandler(
            stream.output_stream, self.on_partial, self.on_final,
            dual_channel=dual,
        )

        await asyncio.gather(
            self._stream_audio(stream),
            self._handler.handle_events(),
        )

    def _thread_target(self):
        """Run the async transcription loop with automatic reconnection."""
        import time as _time
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        attempt = 0

        while self._running:
            try:
                if attempt > 0:
                    delay = min(self._reconnect_delay_base * (2 ** (attempt - 1)), 30)
                    msg = f"⚠️ Transcribe reconnecting (attempt {attempt}/{self._max_reconnect_attempts})..."
                    print(msg)
                    if self.on_status:
                        self.on_status(msg)
                    _time.sleep(delay)
                    # Flush stale audio that accumulated during the outage
                    with self._lock:
                        self._system_buffer = np.empty((0,), dtype=np.float32)
                        self._mic_buffer = np.empty((0,), dtype=np.float32)

                loop.run_until_complete(self._run_transcription())
                # If we get here cleanly, we were stopped intentionally
                break

            except Exception as e:
                attempt += 1
                print(f"Transcription error (attempt {attempt}): {e}")

                if not self._running:
                    break

                if attempt > self._max_reconnect_attempts:
                    msg = "❌ Transcribe connection lost — audio still recording, restart to reconnect"
                    print(msg)
                    if self.on_status:
                        self.on_status(msg)
                    break

            else:
                attempt = 0  # Reset on clean exit

        loop.close()

    def get_audio_devices(self):
        """Return list of available input audio devices."""
        devices = sd.query_devices()
        input_devices = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                input_devices.append((i, d["name"]))
        return input_devices

    def start(self):
        if self._running:
            return
        self._running = True
        self._system_buffer = np.empty((0,), dtype=np.float32)
        self._mic_buffer = np.empty((0,), dtype=np.float32)

        if self.system_device is not None:
            try:
                self._system_stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    device=self.system_device,
                    callback=self._system_callback,
                )
                self._system_stream.start()
                print(f"System audio stream started (device {self.system_device})")
            except Exception as e:
                print(f"Failed to open system audio device {self.system_device}: {e}")
                self._system_stream = None
                if self.on_status:
                    self.on_status(f"⚠️ System audio device failed: {e}")

        if self.mic_device is not None:
            try:
                self._mic_stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    device=self.mic_device,
                    callback=self._mic_callback,
                )
                self._mic_stream.start()
                print(f"Mic stream started (device {self.mic_device})")
            except Exception as e:
                print(f"Failed to open mic device {self.mic_device}: {e}")
                self._mic_stream = None
                if self.on_status:
                    self.on_status(f"⚠️ Microphone device failed: {e}")

        # If both streams failed, stop
        if self._system_stream is None and self._mic_stream is None:
            self._running = False
            if self.on_status:
                self.on_status("❌ No audio devices available — check device settings")
            return

        self._thread = threading.Thread(target=self._thread_target, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        for stream in (self._system_stream, self._mic_stream):
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception as e:
                    print(f"Error closing audio stream: {e}")
        self._system_stream = None
        self._mic_stream = None
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def get_full_transcript(self):
        if self._handler:
            return "\n".join(self._handler.full_transcript)
        return ""
