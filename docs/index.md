# Документация

Язык комплекта: [Русский](ru/index.md) · [English](en/index.md).

[Итог работы за 25 сентября 2026](operations/2026-09-25-summary.md) —
реализованное поведение, проверки и оставшиеся задачи.

Выберите руководство по задаче:

## Использование устройства

- [Быстрый старт и обзор](../README.md)
- [Подключение Wi-Fi, gateway и прошивка StickS3](flash-sticks3.md)
- [Сохранённые Wi-Fi сети и подключение по QR](wifi-profiles-sticks3.md)
- [WireGuard и режим настройки](wireguard-sticks3.md)
- [Голосовые идеи, LLM Wiki и Git-синхронизация Obsidian](voice-knowledge.md)
- [Защита устройства и действия при потере](device-security.md)
- [Формат HTTP-запросов; текущий протокол устройства — v2](protocol.md)

## Разработка и эксплуатация

- [Локальный запуск, тесты, сборка и диагностика](development.md)
- [Компоненты и голосовой поток](architecture.md)
- [Установка локального Codex Agent Service](../deploy/codex-voice-agent.md)
- [Инструкция host-side Codex adapter](../agent_service/README.md)

## Диаграммы

- [Компоненты и границы безопасности](assets/system-overview.svg)
- [Wi-Fi setup и восстановление подключения](assets/wifi-setup-flow.svg)
- В [архитектуре](architecture.md) встроены sequence diagrams для синхронного v1 и асинхронного v2.

> Для актуального поведения ориентируйтесь на обычные руководства и код. Файлы `docs/superpowers/`, исторические handoff-документы и старое техническое задание хранят проектный контекст и могут описывать предыдущие этапы или устройства.
