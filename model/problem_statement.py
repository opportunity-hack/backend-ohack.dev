# Excerpted from messages_service.py
# insert_res = collection.document(doc_id).set({
#         "title": title,
#         "description": description,
#         "first_thought_of": first_thought_of,
#         "github": github,
#         "references": references,
#         "status": status        
#     })

from model.user import User


class ProblemStatement:
    id = None
    title = None
    description = None
    first_thought_of = None
    github = None
    helping = []
    # TODO: references. Pretty sure this is going to be a collection of other entities
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
        return p
    
    def update(self, d):
        props = dir(self)
        for m in props:        
            if m in d:
                setattr(self, m, d[m])
        return
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if m == 'helping':
                all_helping = []

                for h in self.helping:
                    all_helping.append(h.serialize())

                d['helping'] = all_helping
            else:
                d[m] = getattr(self, m)

        return d
    
class Helping:
    user_db_id = None
    problem_statement_id = None
    mentor_or_hacker = None
    timestamp = None
    user: User = None

    @classmethod
    def deserialize(cls, d):
        h = Helping()
        h.user_db_id = d['user_db_id']
        h.problem_statement_id = d['problem_statement_id']
        h.mentor_or_hacker = d['mentor_or_hacker']
        h.timestamp = d['timestamp']
        return h
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if m == 'user':
                d['user'] = self.user.serialize()
            else:
                d[m] = getattr(self, m)

        return d