"""Comunicação simples entre a interface e o trabalho em segundo plano."""


class DownloadCancelado(Exception):
    """Encerramento solicitado pela interface."""


def conferir_cancelamento(cancelamento):
    if cancelamento is not None and cancelamento.is_set():
        raise DownloadCancelado("Download cancelado.")


def saida(silencioso):
    if silencioso:
        return lambda *args, **kwargs: None
    return print
