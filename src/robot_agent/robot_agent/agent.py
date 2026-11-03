"""The agent loop: request -> LLM -> tool calls -> ToolExecutor -> results -> LLM ... (ROS-free).

The loop is deliberately dumb and safe: it executes only what the executor allows, never retries a
refused call on its own, and **ends the whole run the moment a tool result says the robot is in an
emergency stop** (the LLM gets no chance to argue or to try other tools)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from robot_agent.executor import ToolExecutor, ToolResult
from robot_agent.llm import LLM, LLMResponse

SYSTEM_PROMPT = (
    'Sen bir 6 eksenli robot kolunun güvenli kontrol asistanısın. Robotu YALNIZCA sana verilen\n'
    'araçlarla yönetirsin; başka yolun yok (kod çalıştıramazsın, koordinat veremezsin).\n'
    'Kurallar:\n'
    '- Gerekirse önce list_objects ya da get_robot_state ile durumu öğren; tahmin etme.\n'
    '- Bir araç reddedilirse (ok=false) nedenini kullanıcıya olduğu gibi söyle; aynı çağrıyı\n'
    '  değiştirmeden tekrarlama, güvenlik sınırlarını aşmaya çalışma.\n'
    '- Tehlikeli göründüğünde ya da kullanıcı durdurmak istediğinde HEMEN emergency_stop çağır;\n'
    '  acil dur sonrası başka hareket aracı çağırma, kilidi yalnızca bir insan açabilir.\n'
    '- run_plugin ile yalnızca listelenen eklenti araçları çalışır; kamerayı ya da kolu oynatan\n'
    '  araçlar onay ister ve robotun güvenli kutusuna/acil durdurmasına tabidir.\n'
    '- Bir işin başardığını yalnızca araç sonucu ok=true ise söyle.\n'
    '- Kullanıcının dilinde (Türkçe) kısa cevap ver.')


@dataclass
class RunResult:
    text: str
    calls: List[Tuple[str, Any, ToolResult]] = field(default_factory=list)
    halted: Optional[str] = None            # None, 'acil dur' or 'adım sınırı'
    messages: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.halted is None and all(r.ok for _, _, r in self.calls)


class AgentRunner:
    def __init__(self, llm: LLM, executor: ToolExecutor, *, max_steps: int = 8,
                 max_calls_per_step: int = 3, system: str = SYSTEM_PROMPT):
        self._llm, self._executor = llm, executor
        self._max_steps, self._max_calls, self._system = max_steps, max_calls_per_step, system

    def run(self, request: str) -> RunResult:
        messages: List[Dict[str, Any]] = [{'role': 'user', 'content': request}]
        result = RunResult('', messages=messages)
        for _ in range(self._max_steps):
            response: LLMResponse = self._llm.complete(self._system, messages,
                                                       self._executor.specs())
            result.text = response.text or result.text
            content: List[Dict[str, Any]] = []
            if response.text:
                content.append({'type': 'text', 'text': response.text})
            for tc in response.tool_calls:
                content.append({'type': 'tool_use', 'id': tc.id, 'name': tc.name,
                                'input': tc.arguments if isinstance(tc.arguments, dict) else {}})
            messages.append({'role': 'assistant', 'content': content or response.text})
            if not response.tool_calls:
                return result
            blocks, stopped = [], False
            for index, tc in enumerate(response.tool_calls):
                if stopped:
                    outcome = ToolResult(tc.name, 'estopped', 'acil dur: çağrı yürütülmedi')
                elif index >= self._max_calls:
                    outcome = ToolResult(tc.name, 'refused', 'tek yanıtta çok fazla araç çağrısı: '
                                         'yürütülmedi')
                else:
                    outcome = self._executor.execute(tc.name, tc.arguments)
                result.calls.append((tc.name, tc.arguments, outcome))
                blocks.append({'type': 'tool_result', 'tool_use_id': tc.id,
                               'content': outcome.to_llm(), 'is_error': not outcome.ok})
                stopped = stopped or outcome.halts
            messages.append({'role': 'user', 'content': blocks})
            if stopped:
                result.halted = 'acil dur'
                # whatever the model said before the tool calls ("gidiyorum") is no longer true
                result.text = ('Acil dur etkin: işlem durduruldu, yeni hareket verilmedi. Kilidi '
                               'yalnızca bir insan açabilir.')
                return result
        result.halted = 'adım sınırı'
        return result
