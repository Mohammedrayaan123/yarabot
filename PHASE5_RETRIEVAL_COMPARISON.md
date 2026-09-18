# Phase 5 almanac retrieval comparison

The original 13-query list from the earlier session was not present in the repository or supplied attachment. The first 13 rows are a representative replacement set; the last two are the confirmed audit reproductions. These are top retrieval sections, not model answers.

| Query | Before: top section | After: top section | Classifier bypass after |
| --- | --- | --- | --- |
| school hours | SCHOOL HOURS | SCHOOL HOURS | Yes |
| school office hours | Q: What are the school office hours? | SCHOOL HOURS / OFFICE HOURS | Yes |
| fee structure for grade 8 | THIRD CHILD DISCOUNT: Families with a 3rd child (and above) receive a 30% disc | FEE STRUCTURE (per Quarter, in Saudi Riyals, unless noted) | Yes |
| how much is the admission fee | Q: How much is the admission fee? | FREQUENTLY ASKED QUESTIONS (Additional, Official Source) / Q: How much is the  | Yes |
| school transport fees | Department Email Contacts: | FEE STRUCTURE (per Quarter, in Saudi Riyals, unless noted) | Yes |
| quarterly fee due dates | FEE PAYMENT PROCEDURES & DATES | FEE PAYMENT PROCEDURES & DATES | Yes |
| uniform for girls | SCHOOL UNIFORM POLICY | SCHOOL UNIFORM POLICY / GIRLS: | Yes |
| winter uniform | UNIFORM NOTES: | SCHOOL UNIFORM POLICY / UNIFORM NOTES: | Yes |
| teachers day date | FEE PAYMENT PROCEDURES & DATES | No section | No |
| parent teacher meeting date | Late fee: SR 1/day (including VAT) after the due date. If the due date falls o | No section | No |
| where is the school located | SCHOOL CONTACT INFORMATION | SCHOOL CONTACT INFORMATION | No |
| principal visiting hours | VISITING HOURS | SCHOOL HOURS / VISITING HOURS | Yes |
| who is the head of department for mathematics | Q: Who is the Head of Department for a given subject? | HEADS OF DEPARTMENT (HODs) — Teaching Faculty | Yes |
| uniform for boys | UNIFORM NOTES: | SCHOOL UNIFORM POLICY / BOYS: | Yes |
| when do i pay fees | PRE-SCHOOL FEES (Toddlers & Pre-School sections): | FEE PAYMENT PROCEDURES & DATES | Yes |

Content gaps remain: phone policy, canteen/menu, and bullying-reporting procedure. No policy text was added.

Local regression tests verify whole-word matching, heading/table pairing, stricter classifier bypass, and answer-cache invalidation after an almanac file edit. Live answer verification is recorded in the task report.
