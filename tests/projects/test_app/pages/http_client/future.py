from frontik.handler import JsonPageHandler


class Page(JsonPageHandler):
    async def get_page(self):
        state = {
            'second_callback_must_be_async': True,
        }

        def main_callback(json, response):
            self.json.put({
                'main_callback_called': True
            })

        def second_additional_callback(future):
            state['second_callback_must_be_async'] = False

        def additional_callback(future):
            assert future is request_future

            self.json.put({
                'additional_callback_called': True
            })

            self.add_future(request_future, self.finish_group.add(second_additional_callback))
            assert state['second_callback_must_be_async']

        request_future = self.post_url(self.request.host, self.request.path, callback=main_callback)
        self.add_future(request_future, self.finish_group.add(additional_callback))

    async def post_page(self):
        self.json.put({
            'yay': 'yay'
        })
