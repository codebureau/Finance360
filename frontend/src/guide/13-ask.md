# Ask a question

The **Ask** button, available on every screen (top of the sidebar, or beside Menu on a phone), opens a panel where you can type a question about Finance360 in plain words: "where do I enter my salary?", "what is a Rule figure?", "what should I do next?". It finds the places in this Guide that answer it and links you to them, so you can read the page and check.

## Two levels
- **Search only** is the default, and needs nothing set up. The panel is headed **Search the Guide**. It searches this Guide on your server and shows the **best three passages**: the page, the section, a short excerpt and a link to the page. Nothing is sent anywhere, nothing is logged and nothing costs money. It points; it does not write an answer, work anything out or look at [What's due](/) or your records. It also knows the words the app avoids (CONTEXT.md lists them): a question about a "balance" also finds the pages on Valuations.
- **Written answers** is optional, and needs an AI service set up on the host (below). The panel is headed **Ask**. The same passages appear first, at once, and then a short answer written from them appears above them, with the model that wrote it, "Written by <model> from the passages below", and links to the page or screen it relied on. The written answer can use What's due as well, and can be wrong: check the page it cites. If it is slow, or fails, or a limit has been reached, the passages stay on screen.

## Using the panel
- Type a question and press Enter or **Search** (**Ask** with written answers); Shift+Enter or Ctrl+Enter starts a new line. With written answers, follow-ups work: the panel keeps the last three questions and answers, so you can say "and where is that?".
- On a wide screen the panel sits beside the page rather than over it: nothing is dimmed, and you can keep using the screen while you ask about it. Press **Ask** again, or the X, to close it. On a phone it slides over the page, and following a link closes it; opening it again shows the conversation still there.
- **New question** clears the conversation. Reloading the page clears it too. Nothing is saved anywhere (with written answers, the only record is the AI log).
- Closing the panel keeps a question you have typed but not sent; it is there when you open it again.
- If nothing in the Guide matches, it says "I couldn't find that in the Guide" and points to [Start here](/guide).

## What it can and cannot do
- It **explains and points**. It never changes anything: no Account, Valuation, Tax record or setting is touched by a question.
- It only knows what is written in this Guide (and, for a written answer, what What's due says). It does not give tax, legal or investment advice, and it does not look at your balances or work out figures. Questions about what-ifs are **coming later**.
- Search finds words, not meaning. If a search misses, try the word the Guide uses (the page titles are in [Start here](/guide)).

## What is sent, and what is not
At **Search only**, nothing: the question is matched against this Guide on your server.

With **written answers**, each question sends this to the AI service:
- your question, and the last few messages of the same session;
- the best six passages of this Guide for that question (not the whole Guide);
- the wording of the What's due items (which say what could be refreshed, never an amount);
- the screen you are on (for example `/accounts/$accountId`, never an Account's id).

Before anything leaves Finance360, every name it knows (the Household, Members, Accounts and institutions), every Account identifier, every e-mail address, every long run of digits and every amount of money is replaced with a placeholder such as `[ACCOUNT_1]`, and put back in the answer you see.

Never sent: balances, Valuations, Holdings, Prices, Tax records or Documents.

## Seeing exactly what was sent
With written answers, the [AI log](/settings/ai-log) screen (linked from Settings, and from the foot of the Ask panel as "See what was sent") lists every call, newest first: the text exactly as it left Finance360 with the placeholders in it, the reply as it came back, the model used, the tokens and the cost. A call that was refused because a limit was reached is there too. The log is part of the database, so Backups cover it; entries older than a year are removed. At Search only the log stays empty, because nothing was sent.

## Setting up written answers (optional)
Written answers need an AI provider, which is set up once on the host, not in the app. The AI service is billed by its provider, which is why it is off until you choose it; Search only and [reading a page for a Rule figure](/guide/rule-figures) work fully without it. The settings, by name (see `.env.example`):
- `FINANCE360_AI_PROVIDER` chooses the provider. Unset means Search only, the default.
- `FINANCE360_AI_API_KEY_FILE` names a file holding the provider's key. The key is never stored in the repository or shown in the app.
- `FINANCE360_AI_MODEL_ASK` chooses the model that answers questions, `FINANCE360_AI_MODEL_RULE_CAPTURE` the one that reads a page for a Rule figure, and `FINANCE360_AI_MODEL` the default for any other AI task.
- `FINANCE360_AI_MONTHLY_CAP_USD` and `FINANCE360_AI_DAILY_QUESTIONS` set the limits below.
- `FINANCE360_AI_LOG_DAYS` sets how long the AI log is kept.

## Limits
Written answers stop by themselves when they reach **50 questions a day** or **$5.00 a month** in spend, whichever comes first, and the panel says when they start again; search keeps working. These are the defaults; the host can change them. Spend is added up from the AI log, so the screen and the limit cannot disagree.

The daily limit counts written answers only. The monthly spend counts everything AI does, including [reading a page for a Rule figure](/guide/rule-figures) when an AI service reads it, so that does not use up your questions but does count toward the $5.00.
