from model.donation import CurrentDonations, DonationGoals


class Hackathon:
    id = None
    title = ''
    type = ''
    links = []
    teams = []
    donation_current = None
    donation_goals = None
    location = None
    nonprofits = []
    start_date = None
    end_date = None
    image_url = ''

    @classmethod
    def deserialize(cls, d):
        h = Hackathon()
        h.id = d['id']
        h.title = d['title'] if 'title' in d else ''
        h.donation_current = CurrentDonations.deserialize(d['donation_current']) if 'donation_current' in d else None
        h.donation_goals = DonationGoals.deserialize(d['donation_goals']) if 'donation_goals' in d else None
        h.location = d['location'] if 'location' in d else None
        h.start_date = d['start_date']
        h.end_date = d['end_date']
        h.image_url = d['image_url'] if 'image_url' in d else None
        return h
    
    def serialize(self):
        d = {}
        props = dir(self)
        print(f'props {props}') 
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
                pass #TODO
            elif m == 'links':
                pass #TODO
            elif m == 'nonprofits':
                pass #TODO
            elif not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d

