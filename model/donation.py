class DonationGoals:
    id = None
    food = 0
    swag = 0
    prize = 0

    @classmethod
    def deserialize(cls, d):
        g = DonationGoals()
        g.id = d['id'] if 'id' in d else None # bit of a weird one as donation stuff is just part of an aggregate structure which is a hackathon
                                                # therefore, donaion data coming from firebase won't actually have an id
        g.food = d['food'] if 'food' in d else 0
        g.swag = d['swag'] if 'swag' in d else 0
        g.prize = d['prize'] if 'prize' in d else 0
        return g

    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d


class CurrentDonations:
    id = None
    food = 0
    swag = 0
    prize = 0
    thank_you = ''

    @classmethod
    def deserialize(cls, d):
        c = CurrentDonations()
        c.id = d['id'] if 'id' in d else None # bit of a weird one as donation stuff is just part of an aggregate structure which is a hackathon
                                                # therefore, donaion data coming from firebase won't actually have an id
        c.food = d['food'] if 'food' in d else 0
        c.swag = d['swag'] if 'swag' in d else 0
        c.prize = d['prize'] if 'prize' in d else 0
        c.thank_you = d['thank_you'] if 'thank_you' in d else ''
        return c

    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p
        return d