# UI/UX Score Loop - Workflow and Data Management

Date: 2026-07-20

## Test Conditions

- Entry point: `http://127.0.0.1:8091/`
- Browser: installed Google Chrome, controlled headlessly through Playwright
- Clean session: a new browser context for each pass, with a separate fresh context for the mobile pass and no retained cookies or site storage
- Data state: a newly seeded throwaway SQLite database inside a disposable Docker container
- Viewports: `1440x900` desktop and `390x844` mobile
- Colour mode: light
- Required caveat: `Requires competent professional and tax review.`

## Rubric

Every comparable screen was scored from 1 to 5 for:

1. task clarity
2. navigation and orientation
3. action or form usability
4. content hierarchy
5. feedback and error handling
6. accessibility basics
7. responsive fit
8. visual consistency
9. trust and caveat clarity

Maximum score: 45.

## Comparable Scores

| Screen | Task | Navigation | Actions/forms | Hierarchy | Feedback | Accessibility | Responsive | Consistency | Trust | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Review overview - before | 3 | 2 | 3 | 3 | 3 | 3 | 3 | 4 | 5 | 29 |
| Review overview - after | 5 | 5 | 4 | 5 | 4 | 4 | 5 | 5 | 5 | 42 |
| Company setup - before | 4 | 2 | 2 | 2 | 3 | 3 | 3 | 4 | 5 | 28 |
| Company setup - after | 5 | 5 | 4 | 5 | 4 | 4 | 5 | 5 | 5 | 42 |
| Data management - after | 5 | 5 | 4 | 4 | 5 | 4 | 5 | 5 | 5 | 42 |
| Import preview - after | 5 | 5 | 5 | 4 | 5 | 4 | 5 | 5 | 5 | 43 |
| Final review - after | 5 | 5 | 4 | 5 | 4 | 4 | 5 | 5 | 5 | 42 |

No before score is assigned to Data management or Final review because those consolidated screens did not exist in the baseline.

## Captured Screens

Before:

- `before/dashboard-desktop.png`
- `before/dashboard-mobile.png`
- `before/companies-desktop.png`
- `before/companies-mobile.png`

After:

- `after/dashboard-desktop.png`
- `after/dashboard-mobile.png`
- `after/companies-desktop.png`
- `after/companies-mobile.png`
- `after/data-management-desktop.png`
- `after/data-management-mobile.png`
- `after/final-review-desktop.png`
- `after/import-preview-desktop.png`

## Browser Workflow Evidence

- Previewed a JSON company import before applying it.
- Applied one new company and saw the success notice.
- Opened the imported company and saved a contact update.
- Configured and downloaded a companies-only JSON export.
- Selected the imported company after it became unused, typed `DELETE UNUSED`, and removed it.
- Confirmed startup-managed reference customers were not offered for cleanup after the retained fix.
- Confirmed purge remained unavailable by default.
- Confirmed all tested pages returned HTTP 200, retained the decision-support caveat, produced no browser or HTTP errors, and had matching 390 px viewport, document, body and app widths at the mobile viewport.

## Stop Reason

Technical outcome verified after one retained improvement pass. Real human end-user acceptance remains outstanding and is required before release approval.
