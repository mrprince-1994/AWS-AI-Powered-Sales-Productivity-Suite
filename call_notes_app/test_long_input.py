"""
Test script to verify note generation works with a long transcript.
Simulates a ~30 minute call without needing audio.
"""
import time
from summarizer import generate_notes
from storage import save_notes

# Simulate a 30-minute call transcript (~6000 words)
FAKE_TRANSCRIPT = """
Speaker 1: Hey everyone, thanks for joining today's call. We've got a lot to cover. Let me start by going over the agenda. First, we'll review the Q4 results, then discuss the product roadmap for next quarter, and finally talk about the hiring plan.

Speaker 2: Sounds good. Before we dive in, I wanted to flag that we got the updated numbers from finance this morning. Revenue came in at 4.2 million for Q4, which is about 8% above our forecast of 3.9 million. The biggest driver was the enterprise segment, which grew 23% quarter over quarter.

Speaker 1: That's great news. Can you break down the enterprise growth a bit more? I want to understand which accounts drove that.

Speaker 2: Sure. The top three accounts were Acme Corp at 450K, GlobalTech at 380K, and Pinnacle Systems at 290K. Acme expanded their license from 500 to 2000 seats after the pilot program we ran in October. GlobalTech signed a three-year deal which front-loaded some revenue. And Pinnacle added our analytics module on top of their existing platform subscription.

Speaker 1: Excellent. What about the SMB segment? I know we had some concerns there.

Speaker 3: SMB was flat quarter over quarter at about 1.1 million. Churn rate ticked up slightly from 4.2% to 4.8%. The main reasons customers cited were pricing concerns and lack of integrations with their existing tools. We lost about 15 accounts but added 12 new ones.

Speaker 1: That churn increase is concerning. What's our plan to address it?

Speaker 3: We've got a few initiatives. First, we're launching a new starter tier at $29 per user per month, down from $49. Second, the integrations team is prioritizing Salesforce and HubSpot connectors for Q1. Third, we're implementing a customer health scoring system so we can proactively reach out to at-risk accounts.

Speaker 2: I think the health scoring is critical. We should have caught some of those churned accounts earlier. Do we have a timeline for that?

Speaker 3: The engineering team estimates 6 weeks for the MVP. We'd start with usage frequency, support ticket volume, and NPS scores as the initial signals. Phase two would add product adoption metrics.

Speaker 1: Let's make that a priority. Now, moving on to the product roadmap. Sarah, can you walk us through what's planned for Q1?

Speaker 4: Absolutely. We have three major initiatives for Q1. First is the API v3 launch, which includes breaking changes from v2 but adds support for webhooks, batch operations, and improved rate limiting. We're targeting January 15th for the beta and February 28th for GA.

Speaker 1: What's the migration plan for existing API v2 users?

Speaker 4: We'll maintain v2 for 12 months after v3 GA. We're building a migration guide and an automated migration tool that handles about 80% of the common patterns. The remaining 20% will need manual updates. We'll also offer office hours with the developer relations team to help larger customers migrate.

Speaker 2: How many customers are currently on v2?

Speaker 4: About 340 active API consumers. Of those, roughly 60 are heavy users making more than 10,000 calls per day. Those are the ones we'll want to work with directly.

Speaker 1: Makes sense. What's the second initiative?

Speaker 4: The mobile app redesign. We've been getting consistent feedback that our mobile experience is lagging behind competitors. The new design focuses on three things: faster load times, offline support, and a simplified navigation. We've done user testing with 25 customers and the new design scored 4.6 out of 5 compared to 3.1 for the current app.

Speaker 3: Will the mobile redesign include the reporting features? That's the number one request from our SMB customers.

Speaker 4: Yes, we're adding a mobile dashboard with the top 5 most-used reports. Full reporting will still require the desktop app, but the mobile dashboard covers about 70% of what people actually look at on a daily basis.

Speaker 1: And the third initiative?

Speaker 4: AI-powered insights. We're integrating machine learning models to surface anomalies in customer data, predict trends, and generate automated recommendations. This is a big differentiator — none of our direct competitors have this yet. We're partnering with the data science team and targeting a March launch for the first set of features.

Speaker 2: What's the infrastructure cost for the AI features?

Speaker 4: We estimate about $15,000 per month in compute costs for the initial rollout, scaling to about $40,000 as adoption grows. We're using a combination of AWS Bedrock for the LLM components and SageMaker for the custom models.

Speaker 1: That's reasonable given the potential revenue impact. Let's make sure we track the ROI closely. Now, let's talk about hiring. Mike, what's the plan?

Speaker 5: We have 12 open positions across the company. Four in engineering, three in sales, two in customer success, two in marketing, and one in finance. For engineering, we're looking for two senior backend engineers, one ML engineer, and one DevOps engineer. The ML engineer is critical for the AI initiative Sarah mentioned.

Speaker 1: How's the pipeline looking?

Speaker 5: Engineering pipeline is strong — we have about 45 candidates in various stages for the four roles. Sales is tougher. We've been struggling to find enterprise AEs with SaaS experience in our target verticals. We've engaged two recruiting firms to help.

Speaker 2: What's our average time to fill right now?

Speaker 5: Engineering roles are averaging 38 days, which is good. Sales is at 52 days, and customer success is at 41 days. We're trying to get everything under 45 days.

Speaker 1: Let's also discuss the budget implications. With 12 new hires, what's the impact on our burn rate?

Speaker 5: Fully loaded, the 12 positions add about $180,000 per month to our burn. That includes salary, benefits, equipment, and allocated overhead. Given our current runway of 18 months, this brings us down to about 14 months. But with the revenue growth trajectory, we should be cash flow positive before that becomes an issue.

Speaker 2: I want to flag one risk there. If the enterprise pipeline softens or if we see continued SMB churn, that timeline gets tighter. I'd recommend we stage the hiring — bring on the critical roles first and evaluate after Q1 results.

Speaker 1: Good point. Let's prioritize the ML engineer, one senior backend engineer, and two enterprise AEs for immediate hiring. The rest can start in February or March based on Q1 performance.

Speaker 3: Agreed. I also want to mention that we're planning a customer advisory board. We've identified 8 customers who are willing to participate. They'll meet quarterly and provide input on our roadmap. The first meeting is scheduled for January 22nd.

Speaker 1: That's a great initiative. Make sure we include a mix of enterprise and SMB customers.

Speaker 3: Already done. We have 5 enterprise and 3 SMB customers confirmed. We're also offering them early access to new features as an incentive.

Speaker 4: On the technical side, I want to raise a concern about our database performance. We've been seeing increased latency on our main PostgreSQL cluster during peak hours. Average query time has gone from 45ms to 120ms over the past two months. The DBA team recommends either upgrading to a larger instance or implementing read replicas.

Speaker 1: What's the cost difference?

Speaker 4: Upgrading the instance is about $3,000 more per month. Read replicas would be about $5,000 more but give us better scalability long-term. The DBA team recommends read replicas since we're likely to hit the same issue again in 6 months with just an instance upgrade.

Speaker 2: Go with the read replicas. Better to solve this properly now.

Speaker 1: Agreed. Sarah, can you get that implemented this sprint?

Speaker 4: Yes, we can have it done by end of next week. We'll do a staged rollout — read-only queries first, then gradually shift more traffic.

Speaker 1: Perfect. Any other items before we wrap up?

Speaker 5: One more thing — the annual company offsite. We're looking at the first week of March in Austin. Budget is $50,000 for 45 people. We'll do two days of strategic planning and one day of team building. I'll send out a survey for date preferences by end of week.

Speaker 3: Also, quick update on the partnership with DataFlow. They've agreed to a co-marketing arrangement. We'll do a joint webinar in January and a case study featuring their use of our platform. Expected lead generation is about 200 qualified leads.

Speaker 1: Great. Let's make sure the marketing team is aligned on that. Alright, I think we've covered everything. To summarize the key decisions: we're prioritizing the customer health scoring system, staging the hiring plan, going with read replicas for the database, and the company offsite is tentatively first week of March. Action items will be sent out by end of day. Thanks everyone.

Speaker 2: Thanks. One last thing — can we schedule a follow-up on the Q1 forecast? I want to make sure our projections account for the new starter tier pricing.

Speaker 1: Good call. Let's do that Thursday at 2pm. I'll send the invite.

Speaker 3: Works for me. Also, I'll have the customer health scoring requirements doc ready for review by Wednesday.

Speaker 4: And I'll share the API v3 beta timeline with detailed milestones by tomorrow.

Speaker 1: Perfect. Talk to everyone soon. Bye.
""".strip()

# Repeat the transcript to simulate a longer call
LONG_TRANSCRIPT = "\n\n".join([FAKE_TRANSCRIPT] * 5)

print(f"Transcript length: {len(LONG_TRANSCRIPT)} characters, ~{len(LONG_TRANSCRIPT.split())} words")
print("Sending to Claude for note generation (streaming)...\n")

start = time.time()

def on_chunk(text):
    print(text, end="", flush=True)

try:
    notes = generate_notes(LONG_TRANSCRIPT, "Test Customer", on_chunk=on_chunk)
    elapsed = time.time() - start
    print(f"\n\n--- Done in {elapsed:.1f}s ---")
    print(f"Output length: {len(notes)} characters, ~{len(notes.split())} words")

    # Also test saving to docx
    filepath = save_notes("Test Customer", notes)
    print(f"Saved to: {filepath}")
except Exception as e:
    elapsed = time.time() - start
    print(f"\n\nERROR after {elapsed:.1f}s: {e}")
