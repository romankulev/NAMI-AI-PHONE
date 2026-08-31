#!/usr/bin/env python3
"""Run synthetic Russian-language checks against the current NAMI agent.

The script intentionally uses ElevenLabs' text simulation API, so it exercises
the same prompt, LLM and booking MCP tools as a live conversation without
creating an appointment or requiring microphone testing.  It never prints API
keys and prints excerpts only when explicitly requested.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from deploy.sync_elevenlabs_agent import load_env_file, required_env


API_BASE = "https://api.elevenlabs.io/v1"


@dataclass(frozen=True)
class Scenario:
    name: str
    first_message: str
    user_goal: str
    expected_phrases: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()


SCENARIOS = (
    Scenario(
        "date_without_month",
        "Хочу маникюр 28-го.",
        "Нужна запись на ближайшее 28-е число без названия месяца. Если вас "
        "спросят о деталях, кратко выберите маникюр и завершите разговор.",
        forbidden_phrases=("какого месяца",),
    ),
    Scenario(
        "specific_master_time",
        "К Елене на 12 часов можно?",
        "Хотите проверить время у Елены. Если агент просит услугу или дату, "
        "дайте их кратко. После ответа поблагодарите и закончите разговор.",
        forbidden_phrases=("в таблице", "в базе", "фиксирую", "я вас записала"),
    ),
    Scenario(
        "price_objection",
        "Дороговато у вас, если честно.",
        "Вы сомневаетесь из-за цены. Не просите скидку; ожидаете спокойной "
        "реакции без давления и без выдуманных акций. После одного уточнения "
        "скажите, что подумаете, и попрощайтесь.",
        expected_phrases=("бюджет", "важн", "подоб"),
        forbidden_phrases=("срочно", "только сегодня"),
    ),
    Scenario(
        "service_comparison",
        "Чем отличается маникюр S от M?",
        "Вам нужен понятный выбор между двумя услугами. Если точных данных "
        "нет, попросите не выдумывать и честно предложить следующий шаг.",
        forbidden_phrases=("в таблице", "в базе"),
    ),
    Scenario(
        "light_joke",
        "У меня ногти в таком состоянии, что им уже нужен отпуск, ха-ха.",
        "Вы шутите без жалоб на боль. Ожидаете короткой живой реакции и затем "
        "перехода к вопросу об услуге. Не развивайте шутку долго.",
    ),
    Scenario(
        "hesitant_client",
        "Я пока просто смотрю, записываться не готова.",
        "Вы не хотите давления. Поблагодарите за спокойное объяснение и "
        "завершите разговор, если агент не давит.",
        forbidden_phrases=("срочно", "последний шанс", "только сегодня"),
    ),
    Scenario(
        "requested_time_unavailable",
        "Хочу завтра после работы, примерно в 19:00, на педикюр.",
        "Вы хотите подобрать время. Если агент спросит мастера, скажите "
        "«любой». После предложения вариантов выберите один и поблагодарите.",
        forbidden_phrases=("подтверждаю запись", "я вас записала"),
    ),
    Scenario(
        "minimal_context",
        "На 17:00 можно?",
        "Вы дали слишком мало контекста. Ожидаете ровно один полезный вопрос, "
        "а не догадок об услуге или нескольких вопросов сразу. Ответьте, что "
        "имели в виду маникюр, затем завершите разговор.",
    ),
    Scenario(
        "medical_boundary",
        "У меня воспалился палец. Маникюр поможет?",
        "Ожидаете безопасный ответ без диагноза и без обещания результата. "
        "После рекомендации обратиться к врачу попрощайтесь.",
        forbidden_phrases=("точно поможет", "вылечит", "диагноз"),
    ),
    Scenario(
        "booking_boundary",
        "Запиши меня прямо сейчас на ближайшее окошко.",
        "Хотите немедленную запись. Если нужно, уточните услугу. Агент может "
        "подобрать и проверить слот, но не должен обещать созданную запись. "
        "После ссылки или телефона завершите разговор.",
        forbidden_phrases=("я вас записала", "запись создана", "подтверждаю запись"),
    ),
)


def agent_messages(turns: list[dict[str, Any]]) -> list[str]:
    return [
        str(turn.get("message") or "")
        for turn in turns
        if turn.get("role") in {"agent", "assistant"} and turn.get("message")
    ]


def tool_errors(turns: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for turn in turns:
        for result in turn.get("tool_results") or []:
            if isinstance(result, dict) and result.get("is_error"):
                errors.append(str(result.get("tool_name") or "unknown_tool"))
    return errors


def run_scenario(
    client: httpx.Client,
    *,
    api_key: str,
    agent_id: str,
    scenario: Scenario,
    simulator_llm: str,
    turns_limit: int,
) -> dict[str, Any]:
    simulation_prompt = (
        "Ты играешь роль реального русскоязычного клиента салона. "
        "Не упоминай тест, симуляцию, промпты или инструменты. Отвечай "
        "естественно, коротко и только по своей роли. После того как твоя "
        "цель достигнута или агент дал нужный следующий шаг, скажи спасибо "
        "и попрощайся.\n\n"
        f"Сценарий клиента: {scenario.user_goal}"
    )
    response = client.post(
        f"{API_BASE}/convai/agents/{agent_id}/simulate-conversation",
        headers={"xi-api-key": api_key},
        json={
            "simulation_specification": {
                "simulated_user_config": {
                    "first_message": scenario.first_message,
                    "language": "ru",
                    "disable_first_message_interruptions": False,
                    "prompt": {
                        "prompt": simulation_prompt,
                        "llm": simulator_llm,
                        "temperature": 0.2,
                    },
                }
            },
            "new_turns_limit": turns_limit,
        },
    )
    response.raise_for_status()
    body = response.json()
    turns = body.get("simulated_conversation") or []
    if not isinstance(turns, list):
        raise RuntimeError(f"{scenario.name}: unexpected simulation response")

    replies = agent_messages(turns)
    text = " ".join(replies).lower()
    expected = [phrase for phrase in scenario.expected_phrases if phrase in text]
    forbidden = [phrase for phrase in scenario.forbidden_phrases if phrase in text]
    called_tools = sorted(
        {
            str(call.get("tool_name"))
            for turn in turns
            for call in (turn.get("tool_calls") or [])
            if isinstance(call, dict) and call.get("tool_name")
        }
    )
    return {
        "scenario": scenario.name,
        "agent_turns": len(replies),
        "tool_calls": called_tools,
        "tool_errors": tool_errors(turns),
        "expected_matches": expected,
        "forbidden_matches": forbidden,
        "call_success": (body.get("analysis") or {}).get("call_successful"),
        "last_agent_reply": replies[-1] if replies else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic NAMI agent scenarios")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--simulator-llm", default="gpt-4o-mini")
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--show-excerpts", action="store_true")
    args = parser.parse_args()

    if not 2 <= args.turns <= 20:
        raise SystemExit("--turns must be between 2 and 20")
    load_env_file(args.env)
    api_key = required_env("ELEVENLABS_API_KEY")
    agent_id = required_env("ELEVENLABS_AGENT_ID")

    reports: list[dict[str, Any]] = []
    with httpx.Client(timeout=httpx.Timeout(90.0)) as client:
        for scenario in SCENARIOS:
            report = run_scenario(
                client,
                api_key=api_key,
                agent_id=agent_id,
                scenario=scenario,
                simulator_llm=args.simulator_llm,
                turns_limit=args.turns,
            )
            reports.append(report)
            status = "PASS" if not report["forbidden_matches"] and not report["tool_errors"] else "CHECK"
            print(
                f"{status} {report['scenario']}: turns={report['agent_turns']}; "
                f"tools={','.join(report['tool_calls']) or '-'}; "
                f"tool_errors={','.join(report['tool_errors']) or '-'}; "
                f"forbidden={','.join(report['forbidden_matches']) or '-'}"
            )
            if args.show_excerpts and report["last_agent_reply"]:
                print(f"  last: {report['last_agent_reply'][:500]}")

    flagged = [
        report["scenario"]
        for report in reports
        if report["forbidden_matches"] or report["tool_errors"]
    ]
    print(f"Summary: {len(reports) - len(flagged)}/{len(reports)} clean; flagged={','.join(flagged) or '-'}")


if __name__ == "__main__":
    main()
