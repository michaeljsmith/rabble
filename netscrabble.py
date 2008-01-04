#!/usr/bin/python

import threading
import sys

class ServerChannelMessage(object):
	def __init__(self, id, message):
		self.id = id
		self.message = message

class ThreadMessageQueue(object):
	def __init__(self):
		self.items = []
		self.lock = threading.Lock()

	def append(self, message):
		self.lock.acquire()
		try:
			self.items.append(message)
		finally:
			self.lock.release()

	def pop(self):
		self.lock.acquire()
		message = None
		try:
			message = self.items.pop(0)
		finally:
			self.lock.release()
		return message

class ServerChannelMessageQueue(ThreadMessageQueue):
	pass

class ServerChannelMessageQueue(object):
	def __init__(self):
		self.items = []
		self.lock = threading.Lock()

	def append(self, message):
		self.lock.acquire()
		try:
			self.items.append(message)
		finally:
			self.lock.release()

	def pop(self):
		self.lock.acquire()
		message = None
		try:
			message = self.items.pop(0)
		finally:
			self.lock.release()
		return message

class ServerChannelIO(object):
	def __init__(self):
		pass

class ServerChannel(object):
	def __init__(self, id, io):
		self.id = id
		self.io = io

	def get_id(self):
		return self.id

	def listen(self, on_message, on_finished):
		try:
			while not self.io.is_end():
				message = self.io.read_message()
				if message:
					on_message(self.id, message)
		finally:
			on_finished(self.id)

	def cleanup(self):
		self.io.cleanup()

class Server(object):
	def __init__(self, model):
		self.channels = {}
		self.model = model
		self.message_semaphore = threading.Semaphore(0)
		self.message_queue = ServerChannelMessageQueue()
		self.finished = False

	def add_channel(self, channel):
		self.channels[channel.get_id()] = channel

		def channel_finished(id):
			self.message_queue.append(ServerChannelMessage(id, None))
			if not self.channels:
				self.finished = True
			self.message_semaphore.release()
		def message_received(id, message):
			self.message_queue.append(ServerChannelMessage(id, message))
			self.message_semaphore.release()

		channel.listen(message_received, channel_finished)

	def start(self):
		server = self
		class Thread(threading.Thread):
			def run(self):
				while not server.finished:
					server.message_semaphore.acquire()
					message = server.message_queue.pop()
					if not message.message:
						server.cleanup_channel(message.id)
					else:
						server.handle_message(message.id, message.message)
		thread = Thread()
		thread.setDaemon(True)
		thread.start()

	def handle_message(self, id, message):
		self.model.handle_message(id, message.strip())

	def cleanup_channel(self, id):
		channel = self.channels[id]
		del self.channels[id]
		channel.cleanup()

class StdServerChannelIO(ServerChannelIO):
	def __init__(self):
		ServerChannelIO.__init__(self)
		self.eof = False

	def read_message(self):
		message = sys.stdin.readline()
		if not message:
			self.eof = True
		return message

	def write_message(self, message):
		sys.stdout.writeline()

	def is_end(self):
		return self.eof

	def cleanup(self):
		pass

def create_std_server_channel(id):
	io = StdServerChannelIO()
	channel = ServerChannel(id, io)
	return channel

def main():
	class Model(object):
		def handle_message(self, id, message):
			print 'SERVER RECV: %d: %s' % (id, message)
	model = Model()
	server = Server(model)
	std_channel = create_std_server_channel(0)
	server.start()
	server.add_channel(std_channel)

if __name__ == '__main__':
	main()

