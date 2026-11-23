"""PDS projection of the Planner motion pose registry and private asset routes."""
from types import MappingProxyType
from typing import NamedTuple


class PoseRecipe(NamedTuple):
    plan_key: str
    plan_type: str
    reference_key: str
    reference_type: str
    direction_prefix: str
    direction_type: str
    category_label: str


POSE_RECIPES = MappingProxyType({
    'idle': PoseRecipe('planning_idle', 'npc.plan-idle', 'portrait_idle_reference',
                       'npc.portrait-idle-reference', 'portrait_', 'npc.portrait-direction',
                       '아이들 포즈 기획'),
    'combat': PoseRecipe('planning_combat', 'npc.plan-combat', 'portrait_combat_reference',
                         'npc.portrait-combat-reference', 'portrait_combat_', 'npc.portrait-combat-direction',
                         '전투 포즈 기획'),
})
