from datetime import datetime


class JudgeScore:
    def __init__(self):
        self.id = None
        self.judge_id = ''
        self.team_id = ''
        self.event_id = ''
        self.round = ''  # 'round1' or 'round2'
        self.scope_impact = None  # 1-5 points
        self.scope_complexity = None  # 1-5 points
        self.documentation_code = None  # 1-5 points
        self.documentation_ease = None  # 1-5 points
        self.polish_work_remaining = None  # 1-5 points
        self.polish_can_use_today = None  # 1-5 points
        self.security_data = None  # 1-5 points
        self.security_role = None  # 1-5 points
        self.total_score = None  # Calculated from individual scores
        self.feedback = ''  # Optional feedback from judge
        self.is_draft = False
        self.submitted_at = None
        self.created_at = None
        self.updated_at = None

    @classmethod
    def deserialize(cls, d):
        score = JudgeScore()
        score.id = d.get('id')
        score.judge_id = d.get('judge_id', '')
        score.team_id = d.get('team_id', '')
        score.event_id = d.get('event_id', '')
        score.round = d.get('round', '')
        score.scope_impact = d.get('scope_impact')
        score.scope_complexity = d.get('scope_complexity')
        score.documentation_code = d.get('documentation_code')
        score.documentation_ease = d.get('documentation_ease')
        score.polish_work_remaining = d.get('polish_work_remaining')
        score.polish_can_use_today = d.get('polish_can_use_today')
        score.security_data = d.get('security_data')
        score.security_role = d.get('security_role')
        score.total_score = d.get('total_score')
        score.is_draft = d.get('is_draft', False)
        score.feedback = d.get('feedback', '')
        score.submitted_at = d.get('submitted_at')
        score.created_at = d.get('created_at')
        score.updated_at = d.get('updated_at')
        return score
    
    def serialize(self):
        d = {}
        props = dir(self)
        for m in props:
            if not m.startswith('__'):  # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p
        return d

    def calculate_total_score(self):
        """Calculate total score from individual criteria scores"""
        scores = [
            self.scope_impact,
            self.scope_complexity,
            self.documentation_code,
            self.documentation_ease,
            self.polish_work_remaining,
            self.polish_can_use_today,
            self.security_data,
            self.security_role
        ]
        
        # Only calculate if all scores are present
        if all(score is not None for score in scores):
            self.total_score = sum(scores)
        
        return self.total_score

    def to_api_format(self):
        """Convert to the API format expected by frontend"""
        return {
            "scopeImpact": self.scope_impact,
            "scopeComplexity": self.scope_complexity,
            "documentationCode": self.documentation_code,
            "documentationEase": self.documentation_ease,
            "polishWorkRemaining": self.polish_work_remaining,
            "polishCanUseToday": self.polish_can_use_today,
            "securityData": self.security_data,
            "securityRole": self.security_role,
            "total": self.total_score
        }

    @classmethod
    def from_api_format(cls, api_scores):
        """Create JudgeScore from API format"""
        score = cls()
        score.scope_impact = api_scores.get('scopeImpact')
        score.scope_complexity = api_scores.get('scopeComplexity')
        score.documentation_code = api_scores.get('documentationCode')
        score.documentation_ease = api_scores.get('documentationEase')
        score.polish_work_remaining = api_scores.get('polishWorkRemaining')
        score.polish_can_use_today = api_scores.get('polishCanUseToday')
        score.security_data = api_scores.get('securityData')
        score.security_role = api_scores.get('securityRole')
        score.total_score = api_scores.get('total')
        return score

    def __str__(self):
        return f"JudgeScore(id={self.id}, judge_id={self.judge_id}, team_id={self.team_id}, event_id={self.event_id}, round={self.round}, total={self.total_score})"