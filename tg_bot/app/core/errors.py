class DomainError(Exception):
    """Ошибка бизнес-логики, которую бот показывает пользователю понятным текстом."""


class NotProjectMember(DomainError):
    pass


class NotProjectAdmin(DomainError):
    pass


class ProjectNotFound(DomainError):
    pass


class InvalidProjectName(DomainError):
    pass


class InvalidPassword(DomainError):
    pass


class TooManyAttempts(DomainError):
    pass


class InviteInvalid(DomainError):
    pass


class InviteNotFound(InviteInvalid):
    pass


class InviteExpired(InviteInvalid):
    pass


class InviteRevoked(InviteInvalid):
    pass


class RemovedFromProject(InviteInvalid):
    """Пользователя удалили из проекта, а приглашение выпущено до удаления."""


class GroupRebindForbidden(DomainError):
    pass


class CannotRemoveCreator(DomainError):
    pass


class CannotRemoveSelf(DomainError):
    pass


class RateLimited(DomainError):
    pass


class QuestionTooLong(DomainError):
    pass


class EmptyQuestion(DomainError):
    pass


class DocUpdateNotFound(DomainError):
    pass


class QuestionNotFound(DomainError):
    pass


class QuestionAlreadyAnswered(DomainError):
    pass


class QuestionNotAnswered(DomainError):
    pass


class FileTooLarge(DomainError):
    pass


class NoFilesToUpload(DomainError):
    pass
