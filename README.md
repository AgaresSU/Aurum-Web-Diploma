# AurumWeb

Дипломный проект - сайт веб-разработчика и консультанта.

На первом этапе создан проект Django и приложения:

- `core` - общие страницы;
- `content` - услуги и примеры работ;
- `leads` - заявки;
- `accounts` - пользователи;
- `projects` - проекты клиентов.

Во втором этапе добавлены первые HTML-шаблоны и CSS для главной страницы,
услуг, примеров работ и формы заявки.

## Запуск

```text
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```
