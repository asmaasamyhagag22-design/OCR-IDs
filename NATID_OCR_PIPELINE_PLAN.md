# Supplied API client: operation and prerequisites

This document explains the supplied client from its code. It is not the
original team's unpublished design document.

See [the caller setup guide](docs/TEAM_API_CLIENT.md) for commands.

1. Connect to the configured MongoDB database and collection. Select records
   whose `Auto Id_Validation` is absent, null or empty.
2. Replace `<IP_adress>` in each `download folder path` with the configured
   media host; append `/Nid_Front/Nid_Front.jpg`.
3. Download images over HTTP and send batches of up to ten to `POST /engine`.
4. A nonempty `finding` with empty `error_codes` becomes client status
   `success`; other engine items become `ocr_error`. Download and request
   failures have their own statuses. This classification is unchanged from
   the supplied script.
5. In `--dry-run`, log results without updating MongoDB. Otherwise write each
   result to `Auto Id_Validation`. Finish with status counts.

Python packages alone do not provide MongoDB, HTTP image hosting, the OCR
server, applicant records or their images. Those services and data must be
available and correctly configured before the client runs. Images and records
are provided by the team, not included in the source repository.

`success` is not a verified match with an applicant's typed ID. The downstream
system must perform that comparison as declared in the engine contract.
The API client does not compute reading accuracy from ground-truth labels.
