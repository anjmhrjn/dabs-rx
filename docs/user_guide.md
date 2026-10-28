# Rx Surveillance dashboard: user guide

**The data on this dashboard is synthetic.** It comes from a simulated two-hospital system. No real patients, staff, or cabinets are shown.

This dashboard helps pharmacy staff review controlled substance activity in automated dispensing cabinets. It points to records worth a closer look. It does not decide that anyone did anything wrong.

## What you can see
- **Facilities.** You see only the facilities you have been given access to.
- **Patient IDs.** These show as a code like `h_00ab325d8e51` unless you are in the `rx_privileged` group. The code is the same for the same patient, so you can still count and group by patient.

## Daily page
Use this page each morning.
- **Unresolved count discrepancies.** Blind counts that did not match what the cabinet expected, and that no pharmacist has resolved yet. Start here. The table shows the cabinet, the drug, and how many units were over or under.
- **Flagged cabinet events.** The chart shows each day's flagged events by type:

  | Flag | What happened |
  |---|---|
  | Override | Pulled before a pharmacist verified the order. This is common and usually fine in the ED. |
  | Unwitnessed waste | A waste with no witness recorded |
  | Delayed waste | A waste more than 30 minutes after the pull |
  | Pull without active order | No active order for that drug at that time |
  | Pull after discharge | The patient had already been discharged |
  | Pull after transfer | The patient had already left that unit |

- **Below restock level.** Cabinet pockets under their restock level at the end of the latest day.
- **On the FDA shortage list.** Formulary items on the current FDA shortage list, with cabinet stock and days on hand. Days on hand counts only what is in the cabinets.

Use the Facility and Report date filters at the top.

## Monthly page
Use this page for the monthly review.
- **Monthly review list.** The top 10% of nurses for the month, ranked by how far their activity sits above peers on the same unit and shift.
  - Each row lists up to three reasons, for example "waste ratio more than 10 SD above peers (0.31 vs peer median 0.05)", which reads as "this nurse's share of pulled units wasted was 0.31, while the typical peer's was 0.05".
  - "Higher than every one of the peers" means every peer had the same value, usually zero, and this nurse did not.
- **Flagged events by unit.** Trends over the months.
- **Estimated restock spend.** Spend by DEA schedule, and a table by drug. Spend is an estimate from public NADAC prices, not invoice amounts.

To export the review list, open the menu on the table and choose **Download as CSV** or **Download as Excel**.

Each month's list is saved and never changed, so the list for a past month can always be reproduced as it was.

## Data quality page
Rows that failed a data check, such as a missing nurse ID or an unknown cabinet, are kept aside with the name of the check they failed. This page shows how many rows failed, and when. A sudden rise usually points to a feed problem, not to anything a nurse did.

## Reading a score with care
- **A high rank is a reason to look, not a finding.** Check the underlying events in the cabinet system before drawing any conclusion.
- **Small numbers swing hard.** One or two events can rank a nurse high when peers had none. Look at the counts behind the rate.
- **Nurses are compared only with peers on the same unit and shift.** So an ED nurse is compared with other ED nurses.
