# Maintenance log

## 2026-10-25 — Instagram app connection replaced

- The project app is managed in the **Anskar Grotzschnapf Meta Developers
  account**, as confirmed by the project owner. Use that account at
  https://developers.facebook.com/apps/ for future app administration.
- Meta app name: **azerothMoments**. Instagram app name:
  **azerothMoments-IG**. Instagram app ID: `3192643817596007`.
- Connected Instagram account: **@babunigaming**, account ID
  `17841463100734638`, using Instagram API with Instagram Login.
- Replaced the previous app/token connection after Meta returned
  `HTTP 400, code 200: API access blocked`. The precise cause of the old
  account/app restriction was not established.
- Verified the new account connection, renewed the token, updated local
  configuration, and cleared the old access block. The existing Cloudflare
  R2 staging configuration was retained; Instagram webhook subscription
  remains off.
- Confirmed publication of `Screenshot_20261022_133207.png` (screenshot ID 52)
  as a three-panel carousel at 21:25 Stockholm time:
  https://www.instagram.com/p/De7j4QGiPPx/
- Automatic publishing resumed. At migration completion, the earlier failed
  image (screenshot ID 51) had not been retried.

Credentials belong in the private local configuration, not this log.
