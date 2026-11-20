# Connecting Reel to Google (YouTube and Classroom)

Reel publishes to YouTube and Google Classroom with **your own free Google Cloud project**.
You do this once. Afterwards Reel refreshes its access by itself.

Reel keeps the token in **Windows Credential Manager**. It is never kept in a project file,
and it never appears in logs.

## 1. Create the Google Cloud project (free)

1. Open <https://console.cloud.google.com/> with the Google account that owns the YouTube channel.
2. Create a new project, for example "Reel".
3. Go to **APIs & Services → Library** and enable:
   - **YouTube Data API v3**
   - **Google Classroom API**

## 2. Set up the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**.
   - **User type:** choose *Internal* if the academy uses Google Workspace. Otherwise choose
     *External*.
   - **App name:** "Reel". **Support email:** yours.
2. Add these scopes:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube`
   - `https://www.googleapis.com/auth/classroom.announcements`
   - `https://www.googleapis.com/auth/classroom.courseworkmaterials`
3. While the app is in **Testing**, add the channel's account under **Test users**.
4. **Important:** an *External* app in *Testing* status loses access after **7 days**. Reel would
   then stop with "Google access was revoked or has expired". To avoid this, click
   **Publish app** (status *In production*).
   - The app does not need Google's verification for the academy's own few accounts.
   - Google then shows a warning page, "Google hasn't verified this app". Choose
     **Advanced → Go to Reel**.

## 3. Create the OAuth client

1. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. **Application type:** *Desktop app*. **Name:** "Reel".
3. Download the JSON file. Save it as `config\google-client.json`. Git ignores this file; never
   share it.
4. In `.env`, add:
   ```
   GOOGLE_CLIENT_SECRETS_FILE=config/google-client.json
   ```

## 4. Sign in once

1. Run:
   ```powershell
   .venv\Scripts\python -m reel auth google
   ```
2. The browser opens. Sign in with the account that:
   - owns the YouTube channel (choose the channel if you have several), and
   - is a **teacher** in the groups' Classroom courses.
3. Allow all requested access.
4. Back in the terminal you should see **Connected**.

If Google shows **"This service isn't available for your account"** (Russian: «Сервис
недоступен»), one of the requested services is switched off for that account, usually
Classroom. Sign in for YouTube only:

```powershell
.venv\Scripts\python -m reel auth google --only youtube
```

If the page still appears, the account cannot use YouTube through apps. This happens with a
school or company account whose administrator turned YouTube off, or with a supervised child
account. Also check that the browser's account chooser picked the channel's account. When
Classroom is needed later, run `reel auth google` again without `--only`.

To check the connection:
```powershell
.venv\Scripts\python -m reel auth status --check
```

The token is stored as a generic credential named `Reel/google/oauth`. You can see it in
**Control Panel → Credential Manager → Windows Credentials**.

## 5. Things to know

- **Quota:** the YouTube Data API has a free daily quota, and uploads are its most expensive
  calls. The current numbers are on the Cloud console's **Quotas** page. When the quota is used
  up, Reel retries the next day.
- **Private lock (R-5):** YouTube may lock videos uploaded by an API project that has not been
  audited to *private*. Reel flags such a job as `youtube_not_unlisted`. The fix is Google's
  free *YouTube API Services audit* form.
- **Turning access off:**
  1. Run `.venv\Scripts\python -m reel auth forget`. This removes the stored token.
  2. In the Google account, go to **Security → Third-party connections** and remove Reel.
     This revokes the access itself.
- **Another computer or Windows user:** run `reel auth google` there. A token stored in
  Credential Manager belongs to one Windows user on one computer.
- **Development without Credential Manager:** in `config/reel.toml`, set
  `[auth] store = "file"`. Reel then keeps the token in `data/secrets.dev.json` and logs a
  warning every time. Never use this on the academy's server.
