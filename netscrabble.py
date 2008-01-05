#!/usr/bin/python

import threading
import sys
import time
import os
import subprocess
import errno

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

class ServerChannelIO(object):
	def __init__(self):
		pass

class ServerChannel(object):
	def __init__(self, id, io, master_channel=False):
		self.id = id
		self.io = io
		self.master_channel = master_channel

	def get_id(self):
		return self.id

	def listen(self, on_message, on_finished):
		try:
			while not self.io.is_end():
				message = self.io.read_message()
				if message:
					on_message(self.id, message)
		finally:
			print 'channel ended'
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
				print 'server thread exitting'
		thread = Thread()
		thread.start()

	def handle_message(self, id, message):
		self.model.handle_message(id, message.strip())

	def cleanup_channel(self, id):
		channel = self.channels[id]
		del self.channels[id]
		channel.cleanup()
		num_master_channels = len([x for x in self.channels.itervalues()
			if x.master_channel])
		if num_master_channels == 0:
			self.finished = True

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
		sys.stdout.writeline(message)

	def is_end(self):
		return self.eof

	def cleanup(self):
		pass

def create_std_server_channel(id):
	io = StdServerChannelIO()
	channel = ServerChannel(id, io, True)
	return channel

class ChildProcessServerChannelIO(ServerChannelIO):
	def __init__(self, cmd):
		ServerChannelIO.__init__(self)
		self.process = subprocess.Popen([cmd], shell=True,
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)

	def read_message(self):
		message = self.process.stdout.readline()
		return message

	def write_message(self, message):
		self.process.stdin(message + '\n')

	def is_end(self):
		return self.process.stdout.closed

	def cleanup(self):
		self.process.stdin.close()

def create_child_process_server_channel(id, cmd):
	io = ChildProcessServerChannelIO(cmd)
	channel = ServerChannel(id, io)
	return channel

class ScrabbleServerAgent(object):
	def __init__(self, id):
		self.id = id

class ScrabbleServerModel(object):
	def __init__(self):
		self.agents = {}
		self.last_agent_id = 0

	def create_agent(self):
		id = self.alloc_agent_id()
		agent = ScrabbleServerAgent(id)
		return agent

	def handle_message(self, id, message):
		print 'SERVER RECV: %d: %s' % (id, message)

	def alloc_agent_id(self):
		id, self.last_agent_id = self.last_agent_id, self.last_agent_id + 1
		return id

def run_server(args):
	model = ScrabbleServerModel()
	server = Server(model)
	server.start()

	child_process_channel = create_child_process_server_channel(1, 'netscrabble dummy-engine')
	class Thread(threading.Thread):
		def run(self):
			server.add_channel(child_process_channel)
	child_thread = Thread()
	child_thread.start()

	std_channel = create_std_server_channel(0)
	server.add_channel(std_channel)

	print 'main thread exitting'

def run_dummy_engine():
	while True:
		time.sleep(1.0)
		print 'dummy-engine'
		sys.stdout.flush()

def main(argv):
	args_valid = True
	if len(argv) >= 2:
		command = argv[1]
		if command == 'server':
			run_server(argv)
		elif command == 'dummy-engine':
			run_dummy_engine()
		else:
			args_valid = False
	else:
		args_valid = False
		
	if not args_valid:
		print '  Usage: %s <command>' % argv[0]
		print '    where <command> is one of:'
		print '    - server'
		print '    - dummy-engine'

if __name__ == '__main__':
	main(sys.argv)

