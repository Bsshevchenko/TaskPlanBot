from aiogram.fsm.state import State, StatesGroup


class TranscriptionStates(StatesGroup):
    waiting_for_edit = State()


class PlanStates(StatesGroup):
    waiting_for_edit = State()


class WorkStates(StatesGroup):
    selecting = State()


class BacklogStates(StatesGroup):
    waiting_for_voice = State()
    confirming = State()
    waiting_for_edit = State()
