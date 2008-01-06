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
		self.channels[channel.id] = channel

	def listen_to_channel(self, channel_id):
		def channel_finished(id):
			self.message_queue.append(ServerChannelMessage(id, None))
			self.message_semaphore.release()
		def message_received(id, message):
			self.message_queue.append(ServerChannelMessage(id, message))
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
					if not message.message:
						server.cleanup_channel(message.id)
					else:
						server.handle_message(message.id, message.message)
				print 'server thread exitting'
		thread = Thread()
		thread.start()

	def handle_message(self, id, message):
		channel = self.channels[id]
		self.model.handle_message(channel.agent, message.strip(), self)

	def cleanup_channel(self, id):
		channel = self.channels[id]
		del self.channels[id]
		channel.cleanup()
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
		ServerChannelIO.__init__(self)
		self.process = subprocess.Popen([cmd], shell=True,
			stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)

	def read_message(self):
		message = self.process.stdout.readline()
		return message

	def send_message(self, message):
		self.process.stdin.write(message + '\n')

	def is_end(self):
		return self.process.stdout.closed

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
		self.player_index = -1

	def set_game(self, game, player_index):
		self.game = game
		self.player_index = player_index

	def handle_message(self, message, model, server):
		if self.game:
			self.game.handle_message(message, self.player_index, server)

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

	def handle_message(self, agent, message, server):
		agent.handle_message(message, self, server)

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
	def __init__(self, id):
		self.id = id
		self.players = []
		self.to_move = -1

	def handle_message(self, message, player_index, server):
		player = self.players[player_index]
		message = message.strip().lower()
		if message == 'move':
			self.request_move(player, server)
		else:
			server.send_message(player.agent, 'error unknown_command %s' % message)

	def add_player(self, agent):
		index = len(self.players)
		player = ScrabblePlayer(index, agent)
		self.players.append(player)
		return player

	def start(self, server):
		for player_index, player in enumerate(self.players):
			server.send_message(player.agent, 'start_game')
			server.send_message(player.agent, 'player_index %d' % player_index)
			for other_player_index, other_player in enumerate(self.players):
				server.send_message(player.agent,
					'player %d %s' % (other_player_index, other_player.agent.name))

		self.to_move = 0
		self.prompt_turn(server)

	def prompt_turn(self, server):
		for player_index, player in enumerate(self.players):
			server.send_message(player.agent, 'to_move %d' % self.to_move)

	def request_move(self, player, server):
		if self.to_move == player.index:
			for other_player_index, other_player in enumerate(self.players):
				server.send_message(other_player.agent,
					'move %d' % player.index)
			
			self.to_move = (self.to_move + 1) % len(self.players)
			self.prompt_turn(server)
		else:
			server.send_message(player.agent, 'error not_to_move')

def run_server(args):
	model = GameServerModel(ScrabbleGame)
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
	std_channel.agent.set_game(game, std_agent_player.index)
	child_agent_player = game.add_player(child_process_channel.agent)
	child_process_channel.agent.set_game(game, child_agent_player.index)
	model.start_game(game, server)

	server.listen_to_channel(std_channel.id)

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

