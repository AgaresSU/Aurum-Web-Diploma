from .models import Message

AUTO_REPLY_TEXT = (
    "Заявка принята. Я получил описание задачи и вернусь с ответом после разбора. "
    "Если нужно, можно дописать детали прямо в этом диалоге."
)


def add_public_auto_reply(conversation):
    return Message.objects.create(
        conversation=conversation,
        author_role=Message.AuthorRole.SYSTEM,
        body=AUTO_REPLY_TEXT,
    )
