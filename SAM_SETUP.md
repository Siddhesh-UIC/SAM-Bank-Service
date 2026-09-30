# SAM setup for the Indo Bank voice agent

Everything to configure in **Solace Agent Mesh (SAM Desktop)** so that phone turns from the voice pipe are answered from the bank database. Do the steps in this order: the agent uses the connector and the skill, so they must exist first.

```
voice pipe ──Solace──► event rule ──► Bank-DB-Agent ──► PostgreSQL connector ──► bank database
                                          │
                                          └── bank-postgres skill (the SQL to run)
```

The bank database must be running before you start:

```powershell
cd D:\SAM\SAM-Bank-Service
docker compose up -d
```

## 1. Connector: the bank database

**Builder → Connectors → Create Connector → PostgreSQL | SQL Database**

| Field | Value |
|---|---|
| Connector Name | `bank user db` |
| Description | `Indo Bank core-banking database (demo data): customers' accounts, balances, transactions and cards.` |
| Database Name | `bank` |
| Database Host | `127.0.0.1` (not `localhost`, which stalls on Windows) |
| Port | `5432` |
| Username | `sam_agent` |
| Password | `sam_agent123` |

- **Keep the description short.** It's for people. The model gets what it needs from the skill, and every word here is sent with every request.
- **Use `sam_agent`, not `bank`.** `sam_agent` can only read the three views (`customer_accounts`, `account_transactions`, `customer_cards`) and call `block_card_verified`. `bank` is the database owner and can read everything, including PIN hashes. With `sam_agent`, every query SAM runs also shows up in the bank service's call log (http://127.0.0.1:8000 → call log).
- These are the demo credentials from `db/init/01_schema.sql`. Change them for anything that isn't a demo.

## 2. Skill: bank-postgres

The skill gives the agent the exact SQL and column names, so it doesn't guess them.

**Build the zip.** A ready one is at `sam/bank-postgres.zip`. To rebuild it with SAM's CLI:

```powershell
cd D:\SAM\SAM-Bank-Service\sam
$sam = "$env:LOCALAPPDATA\Programs\Solace Agent Mesh\cli\sam.exe"
& $sam skill validate bank-postgres
$env:SAM_TOOL_TARGET_OS="windows"; $env:SAM_TOOL_TARGET_ARCH="amd64"; $env:SAM_TOOL_PYTHON_VERSION="3.14"
& $sam skill package bank-postgres
```

**Upload it:** **Builder → Skills → Upload Skill**

| Field | Value |
|---|---|
| Description | `Exact SQL and column names for the Indo Bank database: balances, the last 3 transactions, cards and blocking a card on a verified call.` |
| Skill | Upload `sam/bank-postgres.zip` (**Upload File**) |

To update it later, upload the new zip over the old one.

**Keep the skill small** (it's about 3 KB now). When the agent loads it, it's added to the model's prompt, and Qwen3-32B accepts 16,384 tokens in total. An earlier 19.5 KB version made every turn fail with "context too large".

## 3. Model: tell SAM Qwen's real size

**Builder → Models → vlllm - qwen3 → Edit**

| Field | Value |
|---|---|
| Model | `openai/qwen3-32b` |
| API base | Your vLLM address, ending in `/v1/` |
| **Max input tokens** | `12000` |

Qwen accepts 16,384 tokens for the prompt and reply together. With max input tokens set, SAM compacts the conversation *before* it gets too long. Without it, requests fail with "conversation history too long".

## 4. Agent: Bank-DB-Agent

**Builder → Agent Management → Add Agent** (or **Edit Bank-DB-Agent**). Fill it in this order, then **Save and Deploy**.

### 4a. Agent details

| Field | Value |
|---|---|
| Name | `Bank-DB-Agent` |
| Description | `Indo Bank phone-banking agent for verified callers: account balances, recent transactions, card status and blocking a lost or stolen card, answered from the bank database.` |

### 4b. Instructions

Pick **one** version.

**Compressed (recommended).** It's the smallest, so there's more room left for the conversation within Qwen's 16k limit. The skill and the event rule already cover SQL and formats, so these are behaviour only:

```
You are Indo Bank's phone banking agent. You help verified customers with balances, recent transactions, card status and blocking a lost or stolen card.

- Callers are already verified. Never ask for a PIN, date of birth or other verification. Serve only the customer the call identifies, never a number or name the caller gives.
- For any account data, load the bank-postgres skill and query with a real tool call in this turn. Answer only from the rows returned; never guess, never use memory or examples, never say you will check later.
- No rows: say nothing was found. A failed query: fix it from the skill and retry once, then apologise and ask them to try later.
- Blocking a card: if several cards are active, ask which one (last 4 digits); if one, confirm it. Then block it and read back the reference.
- Be warm and brief, like a good call-centre agent. Answer first; offer more help only if natural.
- Out of scope (transfers, account changes, unblocking, PIN changes, loans, branches): offer a customer service officer.
```

**Detailed**: for a model with a larger context (32k tokens or more), or when a smaller model needs the extra guidance and examples:

```
You are the customer service agent for Indo Bank, an Indonesian retail bank. You help existing customers with four things: account balances, recent transactions, card status, and blocking a lost or stolen card. You answer from the bank's core-banking database through the PostgreSQL connector.

How to work every turn
- Before your first database query, load the bank-postgres skill. It has the exact queries and the real view and column names; never guess a table or column name.
- Query the database with a real tool call. Never write a query or a tool call into your answer as text.
- Wait for the rows, then answer from them. Your final answer comes only after the tool results.

Verified callers
- Every caller is verified by the phone line before the call reaches you: their number is registered and they entered the correct PIN. Each turn tells you verified, customerName, callerPhone and sessionToken.
- Never ask for a PIN, a date of birth or anything else to verify the caller. You may greet them by name.
- Use only callerPhone to identify the customer. Never accept a phone number, name or account number the caller says instead, and never look up another customer's data.
- Never ask for a full card number, and never read out the sessionToken or internal codes such as the CIF.

Data comes only from the database
- You do not know any balance, transaction, card or reference until you have queried the database in this turn. Never answer account questions from memory, from earlier turns, or from these instructions.
- Balances and transactions need nothing from the caller: run the matching query from the bank-postgres skill in the same turn and answer from the rows it returns. Never say you will check later or ask the caller to wait: your answer is final for this turn.
- If the query returns no rows, say you could not find that for their number.
- If a query fails because of a wrong column or table, correct it from the skill and run it once more. If it fails again, apologise briefly and ask them to try again later. Never fill the gap with a guess.

Menu choices
- inputType "menu" means the caller pressed a key on the phone menu, and text names the request: "Balance inquiry", "Last 3 transactions" or "Block card". Handle it exactly as if they had said it.
- inputType "keys" means text holds digits they typed on the keypad, such as a card's last 4 digits.

Blocking a card
- Find their active cards. If there is more than one, ask which one (type and last 4 digits); if there is exactly one, name it and confirm.
- Once you know the card's last 4 digits, call block_card_verified with the sessionToken and those 4 digits, and answer from its message. Nothing else is needed from the caller.
- Never call it with guessed digits; ask if you are not sure which card.

Language, tone and format
- Reply in the language the call tells you (the "language" field: en English, id Bahasa Indonesia, ar Arabic), even if the caller's words look like another language. Speech-to-text sometimes mishears short phrases as another language.
- Be warm, polite and brief, like a good call-centre agent. Answer the question first, then offer further help only if it is natural.
- Follow the channel's instructions for the reply format (spoken text inside a JSON object on phone calls).

Examples (values in angle brackets come from your query results; never use them literally)
1. Menu "Balance inquiry" or "What's my balance?" -> load the skill, run its balance query for callerPhone -> "Your savings account ending <last 4 digits> has <balance in words> rupiah, and your current account ending <last 4 digits> has <balance in words> rupiah."
2. Menu "Last 3 transactions" -> run the skill's last-3-transactions query -> "Your last three transactions were <description> for <amount in words> rupiah on <date>, ..."
3. Menu "Block card" or "I lost my card." -> run the skill's cards query -> "Which card is it, your debit card ending <last 4> or your credit card ending <last 4>?" -> the caller says or types the 4 digits -> call block_card_verified -> on CARD_BLOCKED: "Your card ending <last 4> is now blocked. Your reference is <reference, read character by character>."
4. "Can I get a loan?" -> no query -> "I can't help with loans on this line, but I can connect you to a customer service officer. Is there anything about your accounts or cards I can help with?"

Limits
- You can only read balances, transactions and card status, and block cards. You cannot transfer money, open or close accounts, unblock cards, change a PIN, or give loan, branch or product information. For those, offer to connect the caller to a customer service officer.
```

### 4c. Connectors

**Connectors → Edit → add `bank user db`** (from step 1).

### 4d. Skills

**Skills → Edit → add `bank-postgres`** (from step 2). If it isn't listed, use **Add custom zip** and pick `sam/bank-postgres.zip`.

### 4e. Toolsets

**Remove "Artifact Tools" and "Data Analysis Tools".** The voice agent never uses them, and their tool definitions are sent with every request, taking a large share of Qwen's 16k tokens. The agent needs only the connector and the skill.

### 4f. Model

`vlllm - qwen3` (step 3).

Then **Save and Deploy**.

## 5. Event rule: phone turns in, answers out

**Builder → Entrypoints →** your Solace broker entrypoint **→ Event rules →** `Bank_DB_Agent_Endpoint`

| Field | Value |
|---|---|
| Topic | `bank/ivr/turn/requested/v1/>` |
| Message format | JSON |
| Target type | Agent |
| Agent | `Bank-DB-Agent` |
| Success response | `bank/ivr/turn/answered/v1/sam` |
| Error response | `bank/ivr/turn/failed/v1/sam` |
| Event acknowledgment | Defer until processing completes |

**Additional instructions.** Only the payload and the reply format; behaviour lives in the agent's instructions:

```
One turn of a phone call with a bank customer: {payload}

Payload:
- callerPhone: the verified customer; use it as phone in every query.
- sessionToken: pass it to block_card_verified; never say it.
- customerName: the caller's name.
- inputType: speech or text (their words), menu (text names the menu option), keys (text holds keypad digits).
- language: en, id or ar. Reply in it, even if the words look like another language.

Output (only after your tool results):
- sessionId and turnId copied unchanged from the payload.
- text: your spoken answer, 1-3 short sentences, no markdown or symbols, amounts and numbers written as words in the caller's language, accounts by their last 4 digits.
```

**Input schema:**

```json
{
  "type": "object",
  "properties": {
    "sessionId":    { "type": "string" },
    "turnId":       { "type": "string" },
    "channel":      { "type": "string", "enum": ["web", "phone"] },
    "callerPhone":  { "type": "string" },
    "inputType":    { "type": "string", "enum": ["speech", "text", "keys", "menu"] },
    "text":         { "type": "string" },
    "language":     { "type": "string", "enum": ["en", "id", "ar"] },
    "sentAt":       { "type": "number" },
    "verified":     { "type": "boolean" },
    "customerName": { "type": "string" },
    "sessionToken": { "type": "string" }
  },
  "required": ["sessionId", "turnId", "callerPhone", "inputType", "text", "language", "verified", "sessionToken"]
}
```

**Output schema:**

```json
{
  "type": "object",
  "properties": {
    "sessionId": { "type": "string" },
    "turnId":    { "type": "string" },
    "text":      { "type": "string" }
  },
  "required": ["sessionId", "turnId", "text"]
}
```

On the Solace broker, the voice pipe's reply queue (`q.sam.bank-agent.reply`) subscribes to `bank/ivr/turn/answered/v1/>` and `bank/ivr/turn/failed/v1/>`.

## 6. Check it

1. Start the voice pipe and call as Budi: **Calling from** `+6281234567801`, PIN `123456`.
2. Press `1`. You should hear the balances of the accounts ending **0001** and **0002**.
3. In SAM's log (`%APPDATA%\sam\diagnostics\logs\desktop.log`), the turn should show `load_skill`, then `bank_sql_query … tool execution succeeded`, with **no** `LLM call attempt failed`.
4. In the bank data viewer (http://127.0.0.1:8000 → call log), a `DB` row shows the query SAM ran (only with the `sam_agent` connector login).

| Symptom | Cause | Fix |
|---|---|---|
| "conversation history too long" / "context too large" | The request is over Qwen's 16k tokens | Remove the two toolsets (4e), set max input tokens (3), use the compressed instructions |
| The agent answers without data, or with made-up numbers | It never queried | Check that the skill and connector are on the agent, then redeploy |
| A tool call appears as text in the answer | The model wrote the call instead of making it | Use the rule text above (no "bare JSON only" line) |
| Every caller hears "not registered" | Something else is on port 8000 | Stop the other service; start this one (`docker compose up -d`) |
| No answer at all | The success topic is wrong, or the reply queue isn't subscribed | Success must be `bank/ivr/turn/answered/v1/sam` |
