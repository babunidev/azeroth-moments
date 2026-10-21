# Cursor Developer Brief: Azeroth Moments

Project: Automatic World of Warcraft Screenshot Publisher Platform: Nobara Linux (Fedora-based) Language: Python 3 Publishing destinations: Instagram and Discord Version: 1.0

## 1. Project overview

Develop a lightweight background application that automatically publishes selected World of Warcraft screenshots to Instagram and Discord.

The application must require no manual interaction after initial configuration.

The intended workflow is simple:

1. The user takes a screenshot while playing World of Warcraft Forever.
2. The user copies or moves the screenshot into `~/Pictures/Azeroth Moments/`.
3. The application detects the new image.
4. The image is automatically prepared and published to Instagram and Discord.
5. The application records the publication results and continues monitoring.

Primary design principle: Sharing a screenshot should require nothing more than placing it in a folder.

## 2. Core architecture

\~/Pictures/Azeroth Moments/

Watched screenshot folder

File watcher and publishing queue

Validation, deduplication, SQLite tracking

Image processing

JPEG conversion, resizing, aspect ratio handling

Instagram

Official Meta API

Discord

Webhook API

Use a modular architecture so additional publishers, such as Bluesky or Tumblr, can be added later.

## 3. Screenshot monitoring

Use Python's `watchdog` library to monitor:

`~/Pictures/Azeroth Moments/`

Requirements:

- Detect new PNG, JPEG and WebP files.
- Handle files copied, moved or saved directly into the directory.
- Wait until a file has finished writing before processing.
- Validate images using Pillow rather than trusting extensions.
- Ignore temporary files, hidden files and unsupported formats.
- Detect duplicates using SHA-256 hashes.
- Prevent duplicate event processing.
- Scan the directory on startup for unprocessed screenshots.
- Do not alter or delete original screenshots.

New files should be published as soon as they are ready, subject to API rate limits.

## 4. Instagram publishing

Use the official Instagram API. Do not use browser automation, Selenium, unofficial private APIs or simulated logins.

Account requirements: An Instagram Professional account (Business or Creator) with the appropriate API configuration and publishing permissions. Meta supports different login flows, and their permission requirements differ.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.postman.com\&sz=32)

Postman API Network

+1



Implementation:

1. Convert screenshots into Instagram-compatible JPEG images.
2. Preserve the original composition whenever possible.
3. Allow configurable padding rather than silently cropping important portions.
4. Upload the prepared image to an explicitly configured HTTPS staging service.
5. Generate a temporary, publicly retrievable image URL for Meta's servers.
6. Create the Instagram media container using the official API.
7. Wait for the container to become ready.
8. Publish the container.
9. Store the resulting media ID and publication status.
10. Delete the staged image after successful publication and a safe retention period.

The Instagram API requires an externally accessible image URL for the image publishing flow. A local filesystem path cannot be used directly.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



Implement hosting as a separate configurable adapter. Prefer private storage with temporary, unguessable URLs. Never expose the user's entire screenshot directory.

If no staging service is configured, mark Instagram as unavailable while allowing Discord publishing to continue.

## 5. Discord publishing

Use a standard Discord incoming webhook.

Requirements:

- Configure a webhook URL through an environment variable.
- Upload screenshots as file attachments using multipart/form-data.
- Include the fixed caption in the message.
- Preserve original resolution when accepted by Discord.
- Resize or compress only when necessary to meet attachment limits.
- Handle HTTP failures and rate limits properly.
- Store the returned Discord message ID when available, using webhook execution with `wait=true`.

Discord supports attachment uploads through its webhook API.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+1



No Discord bot account should be necessary.

## 6. Caption configuration

Default caption:

World of Warcraft Forever

\#WorldOfWarcraft #WoWForever #Azeroth

Use the same fixed caption for Instagram and Discord by default.

Allow captions to be configured separately for each platform.

No AI-generated captions, location detection, filename-based captions or manual post editing are necessary in version 1.0.

## 7. Publishing state and error handling

Use SQLite to store processing state for every screenshot and destination independently.

Suggested states:

`pending`, `processing`, `published`, `retry_pending`, `failed`, `unknown`

Requirements:

- Successful Discord publishing must not depend on Instagram success.
- Failed publications must not cause duplicate successful posts.
- Retry temporary network failures using exponential backoff and jitter.
- Respect HTTP 429 responses and `Retry-After` headers.
- Do not repeatedly retry authentication failures until configuration is corrected.
- Persist the Instagram container ID before attempting publication.
- Reconcile uncertain publishing results before retrying.
- Handle unexpected termination, network disconnection and system restart.
- Record timestamps, file hashes, attempt counts, platform responses and published post identifiers.
- Keep unresolved or permanently failed screenshots available for manual retry.

Exactly-once publication cannot be guaranteed when an API request succeeds but its response is lost. Implement reconciliation and conservative retry behavior to minimize duplicates.

## 8. Configuration and security

Use a `.env` file or equivalent protected local configuration for secrets.

Example configuration:

```
WATCH_DIRECTORY="~/Pictures/Azeroth Moments"

DISCORD_ENABLED=true
DISCORD_WEBHOOK_URL=""

INSTAGRAM_ENABLED=true
INSTAGRAM_ACCESS_TOKEN=""
INSTAGRAM_ACCOUNT_ID=""
INSTAGRAM_API_VERSION=""
INSTAGRAM_STAGING_PROVIDER=""

DEFAULT_CAPTION="World of Warcraft Forever"
```

Additional requirements:

- Never hardcode credentials.
- Exclude `.env` from Git.
- Provide `.env.example` without secrets.
- Ensure sensitive configuration files are accessible only to the user.
- Never log access tokens, webhook URLs or signed staging URLs.
- Validate configuration on startup.
- Use HTTPS for external API communication.
- Refresh or renew Instagram credentials according to the chosen authentication flow.
- Ensure the program does not publicly expose screenshots beyond those selected for sharing.

## 9. Installation on Nobara Linux

Provide a standard Python project using a virtual environment.

Required packages should include:

- `watchdog` for monitoring filesystem events.
- `Pillow` for image validation and processing.
- `requests` or `httpx` for HTTP requests.
- `python-dotenv` for local configuration.

Use SQLite from the Python standard library.

Include:

- `requirements.txt` or `pyproject.toml`.
- An installation script for Nobara Linux.
- A configuration wizard or clear setup instructions.
- A command for testing each publishing connection independently.
- A `--dry-run` option to test screenshot processing without publishing.
- A README with installation, authentication, operation and troubleshooting instructions.

Use a systemd user service to start the publisher automatically when the user logs in.

The service should restart after unexpected failures and work without requiring an open terminal or Cursor session.

## 10. Command-line controls

Provide a minimal command-line interface.

| Command                   | Purpose                       |
| ------------------------- | ----------------------------- |
| `azeroth-moments start`   | Start monitoring              |
| `azeroth-moments status`  | Show current state            |
| `azeroth-moments test`    | Test configured publishers    |
| `azeroth-moments pause`   | Temporarily pause publishing  |
| `azeroth-moments resume`  | Resume publishing             |
| `azeroth-moments retry`   | Retry eligible failed uploads |
| `azeroth-moments history` | Show publishing history       |

Commands must communicate with the running background service or persistent state rather than accidentally starting duplicate watchers.

## 11. Testing and acceptance criteria

The application is complete when:

- A screenshot placed in the folder is detected automatically.
- The original screenshot remains untouched.
- An eligible image is published to both enabled destinations.
- Both platforms receive the configured caption.
- Copying the same image twice does not create duplicate publications.
- A failure on one platform does not block the other.
- A network outage does not cause the application to lose screenshots.
- Restarting the computer does not cause previously published images to be reposted.
- Authentication and API failures produce useful, sanitized logs.
- Discord works independently even when Instagram staging or credentials are not configured.
- The application remains lightweight enough to run during gameplay.

Include unit tests for file readiness, hashing, image conversion, state transitions and retry behavior. Use mocked API responses to test failures without creating real posts.

## 12. Development priorities

Phase 1: Local publisher

Implement file monitoring, image processing, SQLite tracking, configuration and the Discord webhook publisher.

Phase 2: Instagram integration

Implement account authentication, staging storage, media containers, publishing and cleanup.

Phase 3: Reliability and system integration

Add service management, persistent retries, recovery, tests, logging and CLI controls.

## Final instruction to Cursor

Implement **Azeroth Moments v1.0** according to this specification.

Prioritize reliability, simplicity, minimal resource consumption, and maintainable modular code.

Build a functional Discord publishing pipeline first, followed by official Instagram integration. Use only documented APIs. Confirm current Meta authentication, permission, publishing and image requirements before implementing the Instagram adapter.

Where external credentials, Meta configuration or image staging services are required, provide explicit setup instructions and keep the remaining application functional.

Do not introduce a web dashboard, graphical interface, database server, Docker requirement, paid automation service, or unnecessary complexity.

The finished application must run independently of Cursor as a persistent background service on Nobara Linux.

**The central user experience must remain unchanged: put a World of Warcraft screenshot into a folder, and it is automatically shared.**