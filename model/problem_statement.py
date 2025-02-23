# Excerpted from messages_service.py
# insert_res = collection.document(doc_id).set({
#         "title": title,
#         "description": description,
#         "first_thought_of": first_thought_of,
#         "github": github,
#         "references": references,
#         "status": status        
#     })

# "references": [
#     {
#       "link": "https://www.ohack.org/about/history/2020-fall-global-hackathon/2020-fall-non-profits#h.4eksizku2ax5", 
#       "name": "ohack.org"
#     }, 

class ProblemStatement:
    def __init__(self):
        self.id = None
        self.title = ''
        self.description = ''
        self.first_thought_of = None
        self.github = None
        self.helping = []
        self.references = []
        self.events = [] # TODO: Breaking change. This used to be called "events"
        self.status = None

    @classmethod
    def deserialize(cls, d):
        p = ProblemStatement()
        p.id = d['id']
        p.title = d['title']
        p.description = d['description'] if 'description' in d else None
        p.first_thought_of = d['first_thought_of'] if 'first_thought_of' in d else None
        p.github = d['github'] if 'github' in d else None
        p.status = d['status'] if 'status' in d else None

        if 'events' in d:
            p.events = d['events']

        if 'references' in d:
            for h in d['references']:
                p.references.append(Reference.deserialize(h))

        if 'helping' in d:
            for h in d['helping']:
                p.helping.append(Helping.deserialize(h))

        return p
    
    def update(self, d):
        props = dir(self)
        for m in props:        
            if m in d:
                setattr(self, m, d[m])
        return
    
    def serialize(self):
        """Serialize the problem statement and handle nested objects"""
        d = {
            'id': self.id,
            'title': self.title,
            'description': self.description
        }
        
        # Handle events - filter out None values and serialize remaining events
        if hasattr(self, 'events') and self.events:
            d['events'] = []
            for event in self.events:
                if event is not None:
                    if isinstance(event, dict):
                        print(f"Event is already a dict: {event}")
                        d['events'].append(event)
                    else:
                        d['events'].append(event.serialize())
        else:
            d['events'] = []

        # Handle helping
        if hasattr(self, 'helping') and self.helping:
            d['helping'] = [
                h.serialize() if not isinstance(h, dict) else h 
                for h in self.helping if h is not None
            ]
        else:
            d['helping'] = []
            
        # Handle references
        if hasattr(self, 'references') and self.references:
            d['references'] = [
                r.serialize() if not isinstance(r, dict) else r 
                for r in self.references if r is not None
            ]
        else:
            d['references'] = []

        # Add remaining fields that aren't special cases
        for field in ['github', 'status', 'first_thought_of']:
            if hasattr(self, field):
                d[field] = getattr(self, field)

        return d
    
    def __str__(self):
        # Return all the properties of the object
        props = dir(self)
        s = ''
        for m in props:
            if not m.startswith('__'):
                p = getattr(self, m)
                if not callable(p):
                    s += f'{m}={p}, '

        return s
                    
    
class Helping:
    user_id = None
    slack_user = None
    timestamp = None    
    type = None
    # user: User = None # We don't want to serialize this because it could be expensive

    @classmethod
    def deserialize(cls, d):
        h = Helping()
        h.user_id = d['user']
        h.slack_user = d['slack_user']        
        h.type = d['type']
        h.timestamp = d['timestamp']        
        return h
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            # if m == 'user':
            #     d['user'] = self.user.serialize()
            if not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d
    
class Reference:
    link = ''
    name = ''

    @classmethod
    def deserialize(cls, d):
        r = Reference()
        r.link = d['link']
        r.name = d['name']

        return r
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d

