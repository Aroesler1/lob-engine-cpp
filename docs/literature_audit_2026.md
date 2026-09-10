# Primary-source audit through 2026-09-08

This is a targeted literature audit, not an exhaustive claim about every 2026
paper. The repository's empirical scope stays at fifteen symbol-days on three
names. Titles and findings were checked at primary sources. When the inspected
source did not establish a sample date or horizon, that limit is explicit.

| Primary source | Sample and horizon | Finding and implication here |
|---|---|---|
| [Huang, Lehalle and Rosenbaum, JASA 2015, arXiv 1312.0563](https://arxiv.org/abs/1312.0563) | France Telecom and Alcatel-Lucent, Euronext Paris, January 2010 to March 2012; five levels, excluding first/last trading hours; simulated impact examples 1-600 seconds | Intensities depend on queue state within periods of fixed reference price. A good fit to one marginal is not validation of all queue dynamics. The exact reference-price geometry matters for interpreting empty queues. |
| [Wu, Rambaldi, Muzy and Bacry, 2019, arXiv 1901.08938](https://arxiv.org/html/1901.08938v1) | Eurex Bund and DAX futures, 2013-10-01 to 2014-09-30; best-quote events, inter-event times and queue distributions | Adding Hawkes history improves timing and queue distributions in the paper. Here timing improves but queue distributions generally deteriorate. That discrepancy is a result to retain, not evidence that timing alone closes the model. |
| [Bodor and Carlier, 2024, arXiv 2405.18594](https://arxiv.org/abs/2405.18594) | Euro-Bund futures, active days in 2021, 09:00-18:00; five levels, queue/return/signature diagnostics at several intraday scales | State-dependent order-size modeling improves queues and volatility. This is a distinct mechanism from Hawkes timing. The repository's measured net drift does not prove size modeling has no value. |
| [Bodor and Carlier, 2025, arXiv 2501.08822](https://arxiv.org/html/2501.08822v1) | Bund June-2022 contract, March to June 2022, 09:00 to 18:00, five levels each side; event dynamics and simulated execution/post-trade paths | MDQR relaxes independence and adds features and size distributions. Trade-imbalance features improve simulated concavity and relaxation. Its reported Bund results do not establish correctness on this repository's wide-spread MSFT sessions. |
| [Moallemi and Yuan, author paper dated 2016](https://moallemi.com/ciamac/papers/queue-value-2016.pdf) | Nasdaq ITCH, nine liquid large-tick securities, August 2013; Table2 describes21days, a separate calibration passage22days; fill/cancellation/price-change horizon, no universal fixed horizon | Queue value combines execution probability and adverse selection. Retrospective deciles with different order sizes and lifetimes do not isolate the causal value of moving the same order forward. The original README's 2017 refers to SSRN posting, not the paper date. |
| [Cont, Cucuringu and Zhang, QF 2023, arXiv 2112.13213v4](https://arxiv.org/html/2112.13213v4) | Top 100 S&P 500 names chosen at 2019 year-end, 2017 to 2019; intraday impact and one-minute-ahead forecasts | Integrated multi-level OFI improves contemporaneous explanations. Lagged cross-asset OFI can improve forecasts at short horizons. This repository's same-asset fifteen-session comparison addresses a narrower question. |
| [LOB-Bench author repository](https://github.com/peernagy/lob_bench) | Software evaluates real versus generated LOB sequences at distribution and event-response horizons; no new market sample follows from using it | Provides third-party score definitions. Equal book rows on the same input stream imply zero scores by construction. The simulation comparison is the test of realism; reconstruction zeros alone are not. |
| [Noble, Rosenbaum and Souilmi, 2026, arXiv 2603.24137](https://arxiv.org/html/2603.24137v1) | Databento December 2023 to December 2025, large-tick stocks including PFE, INTC, VZ and T, 10:00 to 15:30; event latency and strategy execution | Separates projected book state, empirical race timing and persistent impact feedback. Event-clock correction need not change state trajectories. This provides a concrete 2026 direction for evaluating timing, book shape and fills jointly, without requiring a larger neural model first. |

## Ranked extension and a hypothesis for future data

1. Publish aggregate evidence and make every principal count checkable offline.
   This was the immediate blocker. Direct partial-fill-aware marked payoff and its session counts are now published too.
2. Compare a clock-only correction with a state correction at a frozen model
   setting. A useful prospective hypothesis is: improving event timing alone
   will not materially improve execution calibration on an untouched session.
   Score timing, queues, fills and markout together, with a predefined loss and
   session-level uncertainty. The present results motivated this hypothesis;
   they are not its holdout test.
3. Consider the 2026 state projection and latency-race construction before an
   expensive MDQR extension. Its large-tick scope must remain explicit, and
   any wide-spread extension needs its own validation.

The September8 direct-payoff replay repairs an estimand on the existing sample.
No new simulator fit or prospective test was run.
Subcritical Hawkes offspring norms do not, by themselves, establish stationarity
of a state-dependent queue system. The current classifier rejection and the
queue-size deterioration remain failures, even when timing improves everywhere.
