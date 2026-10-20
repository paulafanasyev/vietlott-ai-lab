# Работа на Vercel

- Сайт: `public/index.html` (вьетнамский), API: `api/index.py` (`/api/status`, `/api/draws`, `/api/export`, `/api/cron`).
- База: Neon через Vercel Storage (переменные `DATABASE_URL*` ставит Vercel; код берёт прямое соединение без pooler).
- Сбор: Vercel Cron ежедневно в 12:45 UTC (19:45 по Вьетнаму, точность на Free — в пределах часа). Перед сбором выполняется `migrate`.
- Ручной запуск: кнопка «Chạy thu thập ngay» (`/api/cron?manual=1`), не чаще раза в 10 минут.
- GitHub Actions из `.github/workflows` больше не нужны (можно отключить в Settings → Actions).
