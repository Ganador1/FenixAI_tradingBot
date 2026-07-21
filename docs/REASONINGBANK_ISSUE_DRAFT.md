# Draft issue for google-research/reasoning-bank

Status: draft only; not published.

Suggested title:

> Discussion: delayed rewards, abstention labels, and multi-agent credit assignment in a non-stationary trading environment

## Proposed issue body

Hello ReasoningBank authors,

We are building an independent, open-source multi-agent cryptocurrency trading
system inspired by ReasoningBank. Specialized technical, microstructure,
sentiment, and visual agents contribute to a final decision, and a separate
risk layer decides whether an actionable signal may be executed.

The environment exposes several cases that are difficult to map cleanly onto a
success/failure memory:

1. **Abstention/HOLD labels.** HOLD is the dominant and often safest outcome.
   Labeling every near-zero move after HOLD as success can inflate an agent's
   score, while treating it as failure can punish correct abstention.
2. **Multi-agent credit assignment.** A single realized market move is an
   outcome for the final decision, but it is weak supervision for every
   contributing agent. Assigning the same label to all agents creates highly
   correlated and potentially misleading scorecards.
3. **Delayed and path-dependent rewards.** Entry quality, adverse excursion,
   protective-order behavior, fees, and exit quality may only be known after
   multiple evaluation windows.
4. **Regime change.** Memories that were useful in one volatility or liquidity
   regime can become actively harmful in another. Similarity alone does not
   guarantee current relevance.
5. **Self-reinforcing retrieval.** Noisy labels can cause low-quality memories
   to be retrieved more often, influence decisions, and then generate more
   similarly biased memories.

We would appreciate the authors' guidance on the following:

- Is there a recommended representation for abstention/HOLD outcomes?
- For a chain of specialized agents, would you store one shared trajectory,
  per-agent memories, counterfactual contribution estimates, or a combination?
- Have you tested time decay or regime-conditioned retrieval in
  non-stationary environments?
- What delayed-reward protocol would you recommend to avoid temporal leakage
  while still revising an experience after its outcome becomes known?
- Are there safeguards you recommend against feedback loops caused by noisy
  judge labels and repeated retrieval?

We can prepare a small reproducible adapter and evaluation protocol that avoids
exchange credentials and account information. If this use case is of interest,
we would be glad to share it and contribute any generally useful findings.

Project: https://github.com/Ganador1/FenixAI_tradingBot

Thank you for publishing the paper and implementation.

## Before publishing

- Confirm that the question is not already answered in existing issues or
  discussions.
- Remove all account balances, trading logs, credentials, and proprietary
  prompts from attachments.
- Attach a minimal synthetic or Testnet-only reproduction, not a Mainnet log.
- Include exact ReasoningBank commit/version and FenixAI commit.
- Explain the labeling protocol and evaluation window precisely.
- Prefer a GitHub Discussion if the repository maintainers reserve issues for
  reproducible bugs.

References:

- Paper: https://arxiv.org/abs/2509.25140
- Official repository: https://github.com/google-research/reasoning-bank

