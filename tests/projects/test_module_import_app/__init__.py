from frontik.app import FrontikApplication


class TestApplication(FrontikApplication):

    def __init__(self, **settings):
        super().__init__(**settings)
