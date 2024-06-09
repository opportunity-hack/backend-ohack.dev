metadata_list = ['id', 'description', 'image']

class Badge:
    id = None
    description = ""
    image = ""
    
    @classmethod
    def deserialize(cls, d):
        b = Badge()
        b.id = d['id']
        b.description = d['description']
        b.image = d['image']
        return b
    
    def serialize_profile_metadata(self):
        d = {}
        props = dir(self)
        for m in metadata_list:        
            if m in props:
                d[m] = getattr(self, m)

        return d
    
    def update_from_metadata(self, d):
        props = dir(self)
        for m in metadata_list:        
            if m in d and m in props:
                setattr(self, m, d[m])
        return