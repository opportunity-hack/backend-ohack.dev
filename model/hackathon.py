# Excerpted from firebase.py
# hackathon = {
#         "title": title,
#         "type": type,
#         "links": links,
#         "teams": teams,
#         "donation_current": donation_current,
#         "donation_goals": donation_goals,
#         "location": location,
#         "nonprofits": nonprofits,
#         "start_date": start_date,
#         "end_date": end_date,
#         "image_url": ""
#     }

class Hackathon:
    id = None
    title = ''
    type = ''
    links = []
    teams = []
    donation_current = 0.0
    donation_goals = 0.0
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
        h.donation_current = d['donation_current'] if 'donation_current' in d else 0.0
        h.donation_goals = d['donation_goals'] if 'donation_goals' in d else 0.0
        h.location = d['location'] if 'location' in d else None
        h.start_date = d['start_date']
        h.end_date = d['end_date']
        h.image_url = d['image_url'] if 'image_url' in d else None
        return h
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if m == 'teams':
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

