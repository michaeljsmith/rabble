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
		id, io = self.id, self.io
		class Thread(threading.Thread):
			def run(self):
				try:
					while not io.is_end():
						message = io.read_message()
						if message:
							on_message(id, message)
				finally:
					on_finished(id)
		thread = Thread()
		thread.setDaemon(True)
		thread.start()

	def cleanup(self):
		self.io.cleanup()

class Server(object):
	def __init__(self, model):
		self.channels = {}
		self.model = model

	def add_channel(self, channel):
		self.channels[channel.get_id()] = channel

	def run(self):
		message_semaphore = threading.Semaphore(0)
		message_queue = ServerChannelMessageQueue()
		def channel_finished(id):
			message_queue.append(ServerChannelMessage(id, None))
			message_semaphore.release()
		def message_received(id, message):
			message_queue.append(ServerChannelMessage(id, message))
			message_semaphore.release()

		for channel in self.channels.itervalues():
			print 'DEBUG: Server.run(): starting channel'
			channel.listen(message_received, channel_finished)

		while self.channels:
			print 'DEBUG: Server.run(): waiting for message'
			message_semaphore.acquire()
			message = message_queue.pop()
			print 'DEBUG: Server.run(): received message'
			if not message.message:
				self.cleanup_channel(message.id)
			else:
				self.handle_message(message.id, message.message)

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
		print 'DEBUG: StdServerChannelIO.read_message'
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
	server.add_channel(std_channel)
	server.run()

if __name__ == '__main__':
	main()

