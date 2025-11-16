# Environment file setup

The trading scripts now load credentials from `.env` files automatically. A
sample template lives at `env_templates/optionstrader.env`. Copy it to your
removable drive and rename it to match the default path Optionstrader looks
for:

```
E:\ENV\optionstrader.env
```

Fill in the placeholders with your actual Bybit demo credentials:

```
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here
TELEGRAM_TOKEN=optional_bot_token
TELEGRAM_CHAT_ID=optional_chat_id
DEMO_BALANCE=10000
```

When the drive is plugged in, Optionstrader automatically reads that file
before looking up any credentials. If you prefer a different location, set one
of the following environment variables before starting Python:

- `OPTIONSTRADER_ENV_PATH`: full path to the `.env` file.
- `OPTIONSTRADER_ENV_DIR`: directory that contains `optionstrader.env`.

Either override allows you to keep multiple profiles or mount points while
still avoiding hard-coded secrets in the scripts.
