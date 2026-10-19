# Engineering changes

Where ECOs wait, who is overloaded, and why they come back, computed from stage history.

## Where is my ECO?

Search any ECO to see every stage it has been through, who held it, for how long, and every
return with the reviewer's words.

## Measures

Cycle time (median and 80th percentile), time per stage by business unit, the share of ECOs
returned at least once, and a quarterly trend, all computed from the history.

## Warnings and bottlenecks

Slow-ECO warnings flag open ECOs that have been in a stage, or open overall, longer than most
have taken. A **bottleneck** is an approver whose median time in a stage is well above the
stage median over many ECOs: a workload question, not a performance verdict. The exact
thresholds are in the [reference](reference#eco-rules).

## Why ECOs are sent back

The AI groups six months of return comments into causes, with quotes and one prevention
control each. Code checks every cited ECO and quote word for word, counts each ECO at most
as often as it was actually returned, and does all the counting. A cause can be sent to
Backlog Pilot as prevention work.
