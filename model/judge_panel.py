from datetime import datetime


class JudgePanel:
    def __init__(self):
        self.id = None
        self.event_id = ''
        self.panel_name = ''
        self.room = None
        self.created_at = None

    @classmethod
    def deserialize(cls, d):
        panel = JudgePanel()
        panel.id = d.get('id')
        panel.event_id = d.get('event_id', '')
        panel.panel_name = d.get('panel_name', '')
        panel.room = d.get('room')
        panel.created_at = d.get('created_at')
        return panel
    
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
        return f"JudgePanel(id={self.id}, event_id={self.event_id}, panel_name={self.panel_name}, room={self.room})"