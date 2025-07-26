from datetime import datetime


class JudgeAssignment:
    def __init__(self):
        self.id = None
        self.judge_id = ''
        self.event_id = ''
        self.team_id = ''
        self.round = ''  # 'round1' or 'round2'
        self.panel_id = None  # For round1 panel assignments
        self.room = None  # For in-person events
        self.demo_time = None  # For round2 scheduling
        self.created_at = None
        self.updated_at = None

    @classmethod
    def deserialize(cls, d):
        assignment = JudgeAssignment()
        assignment.id = d.get('id')
        assignment.judge_id = d.get('judge_id', '')
        assignment.event_id = d.get('event_id', '')
        assignment.team_id = d.get('team_id', '')
        assignment.round = d.get('round', '')
        assignment.panel_id = d.get('panel_id')
        assignment.room = d.get('room')
        assignment.demo_time = d.get('demo_time')
        assignment.created_at = d.get('created_at')
        assignment.updated_at = d.get('updated_at')
        return assignment
    
    def serialize(self):
        d = {}
        props = dir(self)
        for m in props:
            if not m.startswith('__'):  # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p
        return d

    def __str__(self):
        return f"JudgeAssignment(id={self.id}, judge_id={self.judge_id}, event_id={self.event_id}, team_id={self.team_id}, round={self.round})"