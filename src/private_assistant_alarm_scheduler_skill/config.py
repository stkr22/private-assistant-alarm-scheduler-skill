import private_assistant_commons as commons


class SkillConfig(commons.SkillConfig):
    cron_expression: str
    webhook_url: str
