# Keyless Google Play access

Use one Workload Identity pool/provider for the account, and one service account per game. This setup gives each game access only to its own Play app. No Google service-account key is created.

Run the following in Google Cloud Shell with the intended existing Cloud project selected. Replace `PROJECT_ID` with its actual project ID. You need permission to enable APIs and manage service accounts and federation.

```sh
PROJECT_ID="YOUR_GOOGLE_CLOUD_PROJECT_ID"
gcloud config set project "$PROJECT_ID"
gcloud services enable androidpublisher.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

gcloud iam workload-identity-pools create github-godot \
  --location=global --display-name="GitHub Godot releases"

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global --workload-identity-pool=github-godot \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id" \
  --attribute-condition="assertion.repository_owner_id == '16473620' && assertion.event_name == 'workflow_dispatch' && assertion.ref == 'refs/heads/main' && assertion.job_workflow_ref == 'benincasantonio/godot-workflows/.github/workflows/android.yml@refs/tags/v1'"
```

The owner ID above is `benincasantonio`'s numeric ID. Reuse this provider when adding another game. Its service-account binding below authorizes the individual caller repository.

For Plumebound:

```sh
SERVICE_ACCOUNT_ID=plumebound-release
REPOSITORY_ID=1424136923
SERVICE_ACCOUNT="$SERVICE_ACCOUNT_ID@$PROJECT_ID.iam.gserviceaccount.com"
gcloud iam service-accounts create "$SERVICE_ACCOUNT_ID" --display-name="Plumebound Play releases"
gcloud iam service-accounts add-iam-policy-binding "$SERVICE_ACCOUNT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github-godot/attribute.repository_id/$REPOSITORY_ID"
```

For another game, use a new service-account ID and that repository's numeric ID (`gh api repos/OWNER/GAME --jq .id`). Do not give every caller access to every service account.

In **Google Play Console → Users and permissions**, add the service-account email and grant app-specific access to the intended game. Allow viewing app information and releasing apps to testing tracks. Do not grant account-wide admin or production-release access. This Console grant is separate from Cloud IAM.

In the game repository's **Settings → Secrets and variables → Actions → Variables**, set:

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | The chosen Cloud project ID |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-godot/providers/github` |
| `GCP_SERVICE_ACCOUNT` | The game's service-account email |

The caller requests `contents: read` and `id-token: write`. Authentication runs only for publication, immediately before preflight and again after building. Fastlane reads the temporary external-account credential file created by Google's auth action. Its post-step removes that file.

After permissions propagate, an intentionally requested internal release verifies the live connection. Authentication errors must stop the run; they are never treated as an empty Play version history. A draft-only or otherwise incomplete Play app must be completed in Console; the workflow does not weaken release status automatically.

References: [Google deployment federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines), [GitHub reusable-workflow OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows), [Play API setup](https://developers.google.com/android-publisher/getting_started).
