# ZAYYA SEARCH MUSIC

Telegram music discovery bot. It searches YouTube metadata and returns listening links; it does not download or distribute audio files.

## Features
- Mandatory membership check for [@zayyasearchmusic](https://t.me/zayyasearchmusic)
- Search by song title or artist; up to ten numbered results; users tap a number to select a song and open its listening link with duration and listening link
- Free plan: 10 searches per UTC day
- Referral credits: one credit per new referred user
- Premium: 200 ETB / 30 days; payment proof is sent to the configured admin for manual review
- Admin dashboard, user selection and manual premium upgrades
- Admin-created credit coupons

## Deploy on Render
1. Create a Render Blueprint from this repository and review the plan.
2. Set `TELEGRAM_BOT_TOKEN` to the token from BotFather.
3. Set `ADMIN_USER_ID` to your numeric Telegram user ID.
4. Keep `CHANNEL_USERNAME=@zayyasearchmusic`.
5. Approve the Blueprint after checking its worker and persistent disk resources.

## Admin commands
- `/admin` dashboard
- `/users` list users and select one to grant 30 days Premium
- `/upgrade USER_ID [DAYS]` manually upgrade a user
- `/coupon CODE CREDITS [USES]` create a credit coupon
- `/stats` bot statistics

## User commands
- `/start`
- `/redeem CODE`

## Security
Never commit the bot token or admin credentials. Configure secrets in Render Environment. The bot must be an administrator in the channel (or have permission to access member status) for reliable membership verification.
