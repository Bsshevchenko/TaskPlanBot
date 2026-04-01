from aiogram.fsm.state import State, StatesGroup


class TranscriptionStates(StatesGroup):
    waiting_for_edit = State()


class PlanStates(StatesGroup):
    waiting_for_edit = State()
