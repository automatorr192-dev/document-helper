// Адрес бэкенда рентгена.
//
// Пусто — значит API живёт там же, откуда отдана страница: так работает, когда
// статику раздаёт сам FastAPI (docker compose up, Amvera).
//
// На GitHub Pages страница лежит на github.io, а бэкенда там нет — впиши сюда
// https-адрес запущенного API, например туннель для локальной разработки:
//   window.API_BASE = "https://xxxx.trycloudflare.com";
window.API_BASE = "";
