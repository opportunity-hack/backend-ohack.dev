from model.donation import CurrentDonations, DonationGoals


class Hackathon:
    def __init__(self):
        self.id = None
        self.title = ''
        self.type = ''
        self.links = []
        self.teams = []
        self.donation_current = None
        self.donation_goals = None
        self.location = None
        self.nonprofits = []
        self.start_date = None
        self.end_date = None
        self.image_url = ''
        self.event_id = ''

    @classmethod
    def deserialize(cls, d):
        h = Hackathon()
        h.id = d['id']
        h.title = d['title'] if 'title' in d else ''
        h.donation_current = CurrentDonations.deserialize(d['donation_current']) if 'donation_current' in d else None
        h.donation_goals = DonationGoals.deserialize(d['donation_goals']) if 'donation_goals' in d else None
        h.location = d['location'] if 'location' in d else None
        h.start_date = d['start_date']
        h.event_id = d['event_id']
        h.end_date = d['end_date']
        h.image_url = d['image_url'] if 'image_url' in d else None
        h.nonprofits =  []
        h.teams = []
        return h
    
    def serialize(self):
        d = {}
        props = dir(self)        
        for m in props:
            if m == 'donation_goals':
                p = getattr(self, m)
                if p is not None:
                    d[m] = p.serialize()
            elif m == 'donation_current':
                p = getattr(self, m)
                if p is not None:
                    d[m] = p.serialize()
            elif m == 'teams':
                #TODO
                d[m] = []
            elif m == 'links':
                pass #TODO
            elif m == 'teams':
                pass
            elif m == 'nonprofits':
                #TODO
                d[m] = []
            elif not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p                

        return d

