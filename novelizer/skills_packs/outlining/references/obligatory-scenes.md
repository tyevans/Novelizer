# Obligatory scenes reference (Story Grid, per genre)

Populate `BlueprintPlan.obligatory_scenes` with the list matching the
declared `genre`. These are checkable presence requirements, not beat
positions — the Brain/Editor can flag a blueprint whose beat set never
fulfills one of them.

| Genre | Obligatory scenes |
|---|---|
| Love | lovers-meet, break-up (the moment the relationship seems doomed), proof-of-love |
| Thriller | hero-at-mercy-of-villain (the antagonist has the protagonist fully in their power) |
| Crime | discovery (the crime/body/violation is found), exposure (the truth about who and why is revealed) |
| Action | hero-in-jeopardy (life-or-death physical stakes), hero-shows-courage (the decisive act under threat) |

Notes:

- These stack with the beat framework's own required beats — a Love-genre
  three-act blueprint needs both its Break into Two and its lovers-meet
  scene satisfied, and they need not be the same chapter.
- Obligatory scenes have no fixed ideal percentage the way template beats
  do; they're existence checks. If the story tracks a thread or arc for
  the relevant relationship/crime, the fulfilling chapter is typically
  where that thread's `pay_off` or the arc's climax pivot lands.
- Multi-genre stories combine lists (e.g. a crime thriller checks both
  Crime and Thriller obligatory scenes).
