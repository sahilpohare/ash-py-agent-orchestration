"""Platform module - groups all platform resources for route mounting."""

from ironbridge.shared.framework import Module

from .sessions.thread import Thread, Message
from .channels.channel import Channel
from .channels.channel_binding import ChannelBinding
from .identity.tenant import Tenant
from .identity.user import User
from .agents.agent import Agent


class SessionsModule(Module):
    prefix = "/threads"
    resources = [Thread]
    # Message auto-nests via belongs_to(Thread) -> /threads/{id}/messages


class ChannelsModule(Module):
    prefix = "/channels"
    resources = [Channel]
    # ChannelBinding auto-nests via belongs_to(Channel) -> /channels/{id}/bindings


class IdentityModule(Module):
    prefix = "/identity"
    resources = [Tenant, User]


class AgentsModule(Module):
    prefix = "/agents"
    resources = [Agent]


class PlatformModule(Module):
    prefix = ""
    modules = [SessionsModule, ChannelsModule, IdentityModule, AgentsModule]
