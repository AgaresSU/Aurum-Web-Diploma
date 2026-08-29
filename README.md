# AurumWeb

Дипломный проект - сайт веб-разработчика и консультанта.

На первом этапе создан проект Django и приложения:

- `core` - общие страницы;
- `content` - услуги и примеры работ;
- `leads` - заявки;
- `accounts` - пользователи;
- `projects` - проекты клиентов.

## Запуск

```text
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```
