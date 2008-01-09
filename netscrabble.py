#!/usr/bin/python

import threading
import sys
import time
import os
import subprocess
import errno
import re

class ExplodeError(Exception): pass

explode_arg_re = re.compile(r'"(?:.*\\")*.*"|[a-zA-Z0-9_]+')
def explode_args(string):
	arg_re = explode_arg_re
	args = []
	while True:
		string = string.lstrip()
		if not string:
			return args
		m = arg_re.match(string)
		if not m:
			raise ExplodeError()
		args.append(string[m.start():m.end()].strip('"'))
		string = string[m.end():]

class ServerChannelMessage(object):
	def __init__(self, id, command, args):
		self.id = id
		self.command = command
		self.args = args

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
	def __init__(self, id, io, agent, master_channel=False):
		self.id = id
		self.io = io
		self.agent = agent
		self.master_channel = master_channel

	def send_message(self, message):
		self.io.send_message(message)

	def listen(self, on_message, on_finished):
		try:
			while not self.io.is_end():
				message = self.io.read_message()
				if message:
					if not self.master_channel:
						print '%d: %s' % (self.id, message.strip())

					args = None
					error_message = None
					try:
						args = explode_args(message)
					except ExplodeError:
						error_message = 'invalid_syntax'
					if error_message:
						self.send_message('error %s' % error_message)

					if args:
						command, args = args[0], args[1:]
						if command == 'exit':
							break
						on_message(self.id, command, args)
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
		self.channels[channel.id] = channel

	def listen_to_channel(self, channel_id):
		def channel_finished(id):
			self.message_queue.append(ServerChannelMessage(id, None, None))
			self.message_semaphore.release()
		def message_received(id, command, args):
			self.message_queue.append(ServerChannelMessage(id, command, args))
			self.message_semaphore.release()

		channel = self.channels[channel_id]
		channel.listen(message_received, channel_finished)

	def start(self):
		server = self
		class Thread(threading.Thread):
			def run(self):
				while not server.finished:
					server.message_semaphore.acquire()
					message = server.message_queue.pop()
					if not message.command:
						server.cleanup_channel(message.id)
					else:
						server.handle_message(message.id, message.command, message.args)
				print 'server thread exitting'
		thread = Thread()
		thread.start()

	def handle_message(self, id, command, args):
		channel = self.channels[id]
		self.model.handle_message(channel.agent, command, args, self)

	def cleanup_channel(self, id):
		channel = self.channels[id]
		del self.channels[id]
		channel.cleanup()
		self.model.handle_disconnect(channel.agent, self)
		num_master_channels = len([x for x in self.channels.itervalues()
			if x.master_channel])
		if num_master_channels == 0:
			self.finished = True

	def send_message(self, agent, message):
		channel = self.channels[agent.channel_id]
		channel.send_message(message)

class StdServerChannelIO(ServerChannelIO):
	def __init__(self):
		ServerChannelIO.__init__(self)
		self.eof = False

	def read_message(self):
		message = sys.stdin.readline()
		if not message:
			self.eof = True
		return message

	def send_message(self, message):
		sys.stdout.write(message + '\n')

	def is_end(self):
		return self.eof

	def cleanup(self):
		pass

def create_std_server_channel(id, model):
	io = StdServerChannelIO()
	agent = model.create_agent(id)
	channel = ServerChannel(id, io, agent, True)
	return channel

class ChildProcessServerChannelIO(ServerChannelIO):
	def __init__(self, cmd):
		self.eof = False
		ServerChannelIO.__init__(self)
		self.process = subprocess.Popen([cmd], shell=True, close_fds=True,
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

	def read_message(self):
		message = self.process.stdout.readline()
		if not message:
			self.eof = True
		return message

	def send_message(self, message):
		self.process.stdin.write(message + '\n')

	def is_end(self):
		return self.eof

	def cleanup(self):
		self.process.stdin.close()

def create_child_process_server_channel(id, model, cmd):
	io = ChildProcessServerChannelIO(cmd)
	agent = model.create_agent(id)
	channel = ServerChannel(id, io, agent)
	return channel

class GameServerAgent(object):
	def __init__(self, id, channel_id):
		self.id = id
		self.channel_id = channel_id
		self.name = '<UNSET>'
		self.game = None
		self.player_indices = set()

	def set_game(self, game):
		self.game = game

	def add_player_index(self, player_index):
		self.player_indices.add(player_index)

	def handle_message(self, command, args, model, server):
		if self.game:
			self.game.handle_message(command, args, self, server)

	def handle_disconnect(self, model, server):
		if self.game:
			self.game.handle_disconnect(self, server)

	def set_name(self, name):
		self.name = name

class GameServerModel(object):
	def __init__(self, game_factory):
		self.game_factory = game_factory
		self.agents = {}
		self.last_agent_id = 0
		self.games = {}
		self.last_game_id = 0

	def create_agent(self, channel_id):
		id = self.alloc_agent_id()
		agent = GameServerAgent(id, channel_id)
		self.agents[id] = agent
		return agent

	def handle_message(self, agent, command, args, server):
		agent.handle_message(command, args, self, server)

	def handle_disconnect(self, agent, server):
		agent.handle_disconnect(self, server)
		del self.agents[agent.id]

	def create_game(self):
		id = self.alloc_game_id()
		game = self.game_factory(id)
		self.games[id] = game
		return game

	def start_game(self, game, server):
		game.start(server)

	def alloc_agent_id(self):
		id, self.last_agent_id = self.last_agent_id, self.last_agent_id + 1
		return id

	def alloc_game_id(self):
		id, self.last_game_id = self.last_game_id, self.last_game_id + 1
		return id

class ScrabblePlayer(object):
	def __init__(self, index, agent):
		self.index = index
		self.agent = agent

class ScrabbleGame(object):
	def __init__(self, id, word_list):
		self.id = id
		self.players = []
		self.to_move = -1
		self.word_list = word_list

	def handle_message(self, command, args, agent, server):
		if command == 'move':
			self.request_move(agent, server)
		elif command == 'get_word_list':
			self.send_word_list(agent, server)
		else:
			server.send_message(player.agent, 'error unknown_command %s' % command)

	def handle_disconnect(self, agent, server):
		for player_index in agent.player_indices:
			self.players[player_index].agent = None
			self.broadcast(server, 'dropped %d' % player_index)

	def add_player(self, agent):
		index = len(self.players)
		player = ScrabblePlayer(index, agent)
		self.players.append(player)
		return player

	def start(self, server):
		for player_index, player in enumerate(self.players):
			server.send_message(player.agent, 'start_game')
			server.send_message(player.agent, 'player_index %d' % player_index)

		for player_index, player in enumerate(self.players):
			self.broadcast(server, 'player %d %s' %
				(player_index, player.agent.name))

		self.to_move = 0
		self.prompt_turn(server)

	def prompt_turn(self, server):
		self.broadcast(server, 'to_move %d' % self.to_move)

	def request_move(self, agent, server):
		if self.to_move in agent.player_indices:
			self.broadcast(server, 'move %d' % self.to_move)
			
			self.to_move = (self.to_move + 1) % len(self.players)
			self.prompt_turn(server)
		else:
			server.send_message(agent, 'error not_to_move')

	def send_word_list(self, agent, server):
		server.send_message(agent, 'word_count %d' % len(self.word_list))
		for index, word in enumerate(self.word_list):
			server.send_message(agent, 'word %d %s' % (index, word))

	def broadcast(self, server, message):
		for player_index, player in enumerate(self.players):
			if player.agent:
				server.send_message(player.agent, message)

def run_server(args):
	word_list = set(['cat', 'dog', 'apple', 'drape', 'pear'])
	def create_game(id):
		return ScrabbleGame(id, word_list)
	model = GameServerModel(create_game)
	server = Server(model)
	server.start()

	std_channel = create_std_server_channel(0, model)
	std_channel.agent.set_name('player%d' % std_channel.agent.id)
	server.add_channel(std_channel)

	child_process_channel = create_child_process_server_channel(1, model, 'netscrabble dummy-engine')
	child_process_channel.agent.set_name('player%d' % child_process_channel.agent.id)
	server.add_channel(child_process_channel)

	class Thread(threading.Thread):
		def run(self):
			server.listen_to_channel(child_process_channel.id)
	child_thread = Thread()
	child_thread.setDaemon(True)
	child_thread.start()

	game = model.create_game()

	std_agent_player = game.add_player(std_channel.agent)
	std_channel.agent.set_game(game)
	std_channel.agent.add_player_index(std_agent_player.index)

	child_agent_player = game.add_player(child_process_channel.agent)
	child_process_channel.agent.set_game(game)
	child_process_channel.agent.add_player_index(child_agent_player.index)

	model.start_game(game, server)

	server.listen_to_channel(std_channel.id)

	print 'main thread exitting'

class DummyEngine(object):
	class InputError(Exception): pass
	def run(self):
		while True:
			try:
				for command, args in self.read_commands():
					pass
			except DummyEngine.InputError, e:
				print 'Invalid command syntax received from server: "%s"' % dir(e)
				sys.stdout.flush()
		print 'exitting'
		sys.stdout.flush()
	
	def read_commands(self):
		while True:
			message = sys.stdin.readline()
			if not message:
				break
			args = None
			try:
				args = explode_args(message)
			except ExplodeError:
				raise InputError(message)

			if args:
				command, args = args[0], args[1:]
				yield command, args

def main(argv):
	args_valid = True
	if len(argv) >= 2:
		command = argv[1]
		if command == 'server':
			run_server(argv)
		elif command == 'dummy-engine':
			DummyEngine().run()
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

