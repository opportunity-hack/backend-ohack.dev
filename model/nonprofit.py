# Excerpted from messages_service.py
#    insert_res = collection.document(doc_id).set({
#         "contact_email": [email], # TODO: Support more than one email
#         "contact_people": [name], # TODO: Support more than one name
#         "name": npoName,
#         "slack_channel" :slack_channel,
#         "website": website,
#         "description": description,
#         "problem_statements": problem_statements
#     })

class Contact:
    id = None
    email = ''
    name = ''

    @classmethod
    def deserialize(cls, d):
        c = Contact()
        c.id = d['id']
        c.email = d['email']
        c.name = d['name']

    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d

class Nonprofit:
    id = None
    name = ''
    slack_channel = ''
    website = ''
    description = ''
    need = None

    contacts = []
    # TODO: problem_statements = []

    @classmethod
    def deserialize(cls, d):
        n = Nonprofit()
        n.id = d['id']
        n.name = d['name']
        n.slack_channel = d['slack_channel']
        n.website = d['website']
        n.description = d['description']
        n.need = d['rank'] if 'rank' in d else None
        n.need = d['need'] if 'need' in d else n.need # New prop should win

        if 'contact_email' in d or 'contact_name' in d:
            c = Contact()
            c.name = d['contact_name'] if 'contact_name' in d else None
            c.email = d['contact_email'] if 'contact_email' in d else None
            n.contacts.append(c)

        return n
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if m == 'contacts':
                all_contacts = []
                for c in self.contacts:
                    all_contacts.append(c.serialize())

                d['contacts'] = all_contacts

            elif m == 'problem_statements':
                pass #TODO
            
            elif not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d