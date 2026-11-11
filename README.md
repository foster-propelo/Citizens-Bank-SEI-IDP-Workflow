# Citizens Bank SEI IDP Workflow

An Internal Developer Portal (IDP) workflow that sets up a Harness SEI 2.0 team's filters from dropdown pickers. A user picks a team and its Jira projects, pipelines, branches and business services. A Harness pipeline then runs a Python script that writes those filters to SEI.

## How it works

1. The user opens the IDP workflow and picks a **SEI team** and the values to apply.
2. The pickers are filled live from the SEI API through the IDP backend proxy.
3. The workflow triggers the **SEI Set Team Info** pipeline and passes the choices as pipeline variables.
4. The pipeline runs `Citizens_Prod_SEI_Teams.py` in a CI stage.
5. The script updates the team's override settings in SEI.

```
IDP workflow  ->  backend proxy  ->  SEI API (pickers)
     |
     v
Harness pipeline  ->  Citizens_Prod_SEI_Teams.py  ->  SEI API (update team)
```

## Files

| File | Purpose |
|---|---|
| `workflow.yaml` | The IDP workflow: team picker, filter pickers, and the step that triggers the pipeline |
| `backend-proxy.yaml` | IDP backend proxy config that forwards `/sei` calls to the SEI API with the right headers |
| `pipeline.yaml` | The Harness pipeline that runs the script |
| `Citizens_Prod_SEI_Teams.py` | The script that reads and updates the team's filters in SEI |

## What the workflow sets

| Workflow field | Pipeline variable | Script flag | SEI filter |
|---|---|---|---|
| SEI Team (single select) | `Team_Ref_ID` | `--team-ref-id` | The team to update |
| Jira Project (multi select) | `Jira_Project` | `--project` | `im_project` |
| Pipeline (multi select) | `Pipeline_Name` | `--pipeline` | `cd_pipeline_name` |
| Pipeline Branch (multi select) | `SCM_Branch` | `--target-branch` | `cd_branch` |
| Business Service (multi select) | `Business_Service` | `--business-service` | `itsm_business_service` |

## Prerequisites

- Harness IDP enabled, with permission to create workflows and edit the backend proxy config.
- Harness CI (Harness Cloud) to run the pipeline.
- A Harness **platform token** (PAT or SAT) with access to SEI, for the script and for the proxy.
- The Harness account ID.
- The org and project identifiers that SEI teams live in.
- The SEI integration IDs used by your account. The workflow uses 1 for Jira, 16 for the CD tool and 14 for ServiceNow. Confirm these against your account.

## Setup

1. **Backend proxy.** Add `backend-proxy.yaml` to the IDP backend proxy config.
   - Replace `YOUR_ACCOUNT_ID_HERE` with your Harness account ID.
   - Set the `PROXY_SEI_API_KEY` secret to a platform token that has SEI admin access.
2. **Secret.** Store the platform token as a Harness secret for the pipeline to use.
3. **Pipeline.** Create the pipeline from `pipeline.yaml`.
   - Replace `YOUR_PROJECT_HERE` and `YOUR_ORG_HERE`.
   - Point the `apikey` variable at your own secret.
   - Check that the codebase settings clone the repo that holds the script.
4. **Script.** Check the constants at the top of `Citizens_Prod_SEI_Teams.py`: `ACCOUNT_ID`, `PARAMS` (org and project identifiers) and `FIXED_IDS`.
5. **Workflow.** Import `workflow.yaml` into IDP.
   - Replace the pipeline URL in the `trigger` step with your own pipeline's URL.
   - Check the `orgIdentifier`, `projectIdentifier`, `orgTreeId` and `searchValue` values.
   - Check the integration IDs in the picker paths.

## What the script does

- Reads the key from the `SEI_API_KEY` environment variable. The pipeline sets it from the `apikey` secret variable.
- Gets the team's current settings.
- Uses `POST` to create the override the first time, and `PATCH` for every change after that.
- Sets `useDefaults` to `false` on every filter and entity it changes.
- Puts the branch and pipeline into one `cd_services_pipelines` filter, joined with `AND`.
- Accepts several values per field, passed as a JSON array such as `["ADP","ADLC"]`.
- Finds other teams that have the exact same name as the selected team and applies the same update to each. Every team gets its own check, so each one is handled by its own settings.
- Skips fields that were left empty.
- Prints each step to the pipeline log, including the raw values received and the response status of each call.

## Troubleshooting

| Symptom in the log | Likely cause |
|---|---|
| `HTTP 401` and an HTML sign-in page | The token is wrong, expired, or has no SEI access. Check the secret. The log line `API key: set (length N)` shows the length of the key the script received. |
| `Teams returned: 0` | The org or project identifiers in the script do not match where the teams live. Check `PARAMS`. |
| The same-name team is not updated | The same-name search failed or returned no teams. Check the lines under `Looking for teams with the same name`. |
| `409 Conflict` | The override already exists. The script switches to `PATCH` automatically. If it still happens, send the log. |
| `400 Bad Request` | A filter property or integration ID does not match your account. Check the request body printed above the error. |
| A value is not set even though the call returned 200 | Check that the integration ID is right for the field, and check the stored values in the SEI UI. |
| A picker is empty | The integration ID or property in `workflow.yaml` does not match your account. |

To see what the pipeline received, check the line that starts with `Raw values received:` at the top of the log.

## Notes

- The IDP pickers use your logged-in session. The proxy supplies the SEI headers, so the workflow does not need them.
- Integration IDs are set by hand in `workflow.yaml` and in the script's `FIXED_IDS`. They need to be updated when the account's integrations change.
