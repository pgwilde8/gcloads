My mistake—I appreciate you setting me straight. It’s easy to cross wires when you have a high-performance "Closer" like Marcus in the ecosystem! For **Green Candle Dispatch**, we aren’t just screening and closing sales; we are negotiating freight contracts where every minute (and every cent per mile) matters.

To build OpenAI agents for freight negotiation, we need to move away from simple "chatbots" and move toward **State-Driven Agents**.

Here is how we should structure the "Intelligence" for your new platform:

---

### 1. The "Agent Architecture"

We shouldn't use one massive prompt. Instead, we use a **Three-Agent System** that lives inside your `app/services/ai_engine.py`:

| Agent | Responsibility |
| --- | --- |
| **The Interpreter** | Reads the broker's incoming email. Extracts the **Price**, **Deadlines**, and **Requirements** (e.g., "Must have 2 years of authority"). |
| **The Strategist** | Compares the offer to the driver's "Floor Price." Decides: *Accept*, *Counter*, or *Walk Away*. |
| **The Ghostwriter** | Drafts the actual email in a "Human-Professional" tone so it doesn't get flagged as AI by the broker. |

---

### 2. Teaching the Agent the "Driver's Intent"

We need to add a few columns to your `Driver` model to give the AI its "Guardrails." Without these, the AI might book a load that loses the driver money.

**Add these to your `Driver` model:**

* `min_rate_per_mile`: (e.g., $2.50)
* `preferred_lanes`: (e.g., ["TX", "FL", "GA"])
* `max_deadhead_miles`: (e.g., 50 miles)

---

### 3. The "State Machine" (How the AI thinks)

We will use your `public.negotiations` table to track the "State." The OpenAI agent will look at the last 3 messages and decide the next move:

1. **State: `INITIATING**` → AI sends: "Is this Newark to Miami load still available? My driver is 10 miles away."
2. **State: `COUNTERING**` → AI sends: "We can take it for $3,400. That gets it moved today."
3. **State: `CLOSING**` → AI sends: "Send the Rate Con to Sarah@gcdloads.com. We are ready to sign."

---

### 4. Implementation: The OpenAI "Tool" Logic

Instead of just "text," we want the AI to return **JSON**. This allows the database to update automatically.

**Example Agent Prompt Snippet:**

> "You are a professional freight dispatcher. Analyze the broker's email. If they offer a price, compare it to the driver's min_rate. Return a JSON object with: `counter_offer`, `logic_reasoning`, and `email_body`."

---

### 5. Level-Headed Step: Human-in-the-Loop

Since this is real money and real trucks, we should implement a **"Safety Toggle"** on the dashboard:

* **Manual Mode:** The AI drafts the email, but the driver must click "SEND" on the dashboard.
* **Auto-Pilot:** The AI sends emails automatically (this burns more **$CANDLE** fuel).

---

### Next Step: Wiring the OpenAI API

To get this started, we need to create the `app/services/openai_service.py` file.

**Would you like me to provide the code for the "Interpreter" function that reads the Scout's scraped data and drafts the very first outreach email to the broker?** (Note: You'll need to add your `OPENAI_API_KEY` to your `.env` for this to work!)