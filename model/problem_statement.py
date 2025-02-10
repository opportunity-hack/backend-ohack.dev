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

from model.user import User


class ProblemStatement:
    id = None
    title = None
    description = None
    first_thought_of = None
    github = None
    helping = []
    references = []
    hackathons = [] # TODO: Breaking change. This used to be called "events"
    status = None

    @classmethod
    def deserialize(cls, d):
        p = ProblemStatement()
        p.id = d['id']
        p.title = d['title']
        p.description = d['description'] if 'description' in d else None
        p.first_thought_of = d['first_thought_of'] if 'first_thought_of' in d else None
        p.github = d['github'] if 'github' in d else None
        p.status = d['status'] if 'status' in d else None


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
        d = {'debug': True}
        props = dir(self)
        for m in props:
            if m == 'helping':
                all_helping = []

                for h in self.helping:
                    if isinstance(h, dict):
                        all_helping.append(h)
                    else:
                        all_helping.append(h.serialize())

                d['helping'] = all_helping

                pass

            elif m == 'hackathons':
                all_hackathons = []

                for thon in self.hackathons:
                    all_hackathons.append(thon.serialize())

                d['hackathons'] = all_hackathons

            elif m == 'references':
                all_references = []

                for r in self.references:
                    if isinstance(r, dict):
                        all_references.append(r)
                    else:
                        all_references.append(r.serialize())

                d['references'] = all_references

            # TODO: Extract is_serializable(self_object, name)
            elif not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

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