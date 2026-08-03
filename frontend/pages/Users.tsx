import React, { useState, useEffect } from 'react';
import { Users as UsersIcon, UserPlus, Edit, Trash2, Shield, Key, RefreshCw, AlertCircle } from 'lucide-react';
// import { useAuthStore } from '../stores/authStore';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Select } from '../components/ui/Select';
import { Badge } from '../components/ui/Badge';
import { Alert, AlertTitle, AlertDescription } from '../components/ui/Alert';
import { Modal } from '../components/ui/Modal';
import { authHeaders } from '@/lib/auth';

interface User {
  id: string;
  email: string;
  username: string;
  role: 'admin' | 'trader' | 'viewer';
  status: 'active' | 'inactive';
  created_at: string;
  last_login?: string;
  permissions: string[];
  profile?: {
    first_name?: string;
    last_name?: string;
  };
  settings?: {
    notifications_enabled: boolean;
    theme: 'light' | 'dark' | 'auto';
  };
}

/*
interface Role {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  is_system: boolean;
}
*/

export const UsersPage: React.FC = () => {
  // const { user: currentUser } = useAuthStore();
  const [users, setUsers] = useState<User[]>([]);
  // const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRole, setSelectedRole] = useState<string>('all');
  const [selectedStatus, setSelectedStatus] = useState<string>('all');
  const [showUserModal, setShowUserModal] = useState(false);
  // const [showRoleModal, setShowRoleModal] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [resettingUser, setResettingUser] = useState<User | null>(null);
  const [deletingUser, setDeletingUser] = useState<User | null>(null);
  const [adminPassword, setAdminPassword] = useState('');
  const [issuedToken, setIssuedToken] = useState<{
    token: string;
    email: string;
    purpose: 'setup' | 'reset';
    expiresAt: string;
  } | null>(null);
  // const [editingRole, setEditingRole] = useState<Role | null>(null);

  // Form states
  const [userForm, setUserForm] = useState({
    email: '',
    role: 'viewer' as User['role'],
    status: 'active' as User['status'],
    first_name: '',
    last_name: '',
    admin_password: ''
  });

  /*
  const [roleForm, setRoleForm] = useState({
    name: '',
    description: '',
    permissions: [] as string[]
  });
  */

  useEffect(() => {
    fetchUsersData();
  }, []);

  const fetchUsersData = async () => {
    try {
      setLoading(true);
      setError(null);

      const usersResponse = await fetch('/api/auth/users', {
        headers: authHeaders()
      });

      if (!usersResponse.ok) {
        throw new Error('Failed to fetch users data');
      }

      const usersData = await usersResponse.json();

      setUsers(usersData);
      // setRoles(rolesData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch users data');
    } finally {
      setLoading(false);
    }
  };

  const getRoleBadgeColor = (role: string) => {
    switch (role) {
      case 'admin': return 'error';
      case 'trader': return 'success';
      case 'viewer': return 'default';
      default: return 'default';
    }
  };

  const getStatusBadgeColor = (status: string) => {
    switch (status) {
      case 'active': return 'success';
      case 'inactive': return 'default';
      default: return 'default';
    }
  };

  const filteredUsers = users.filter(user => {
    const matchesSearch = searchQuery === '' || 
      user.email.toLowerCase().includes(searchQuery.toLowerCase()) ||
      user.username.toLowerCase().includes(searchQuery.toLowerCase()) ||
      user.profile?.first_name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      user.profile?.last_name?.toLowerCase().includes(searchQuery.toLowerCase());
    
    const matchesRole = selectedRole === 'all' || user.role === selectedRole;
    const matchesStatus = selectedStatus === 'all' || user.status === selectedStatus;
    
    return matchesSearch && matchesRole && matchesStatus;
  });

  const handleCreateUser = () => {
    setEditingUser(null);
    setUserForm({
      email: '',
      role: 'viewer',
      status: 'active',
      first_name: '',
      last_name: '',
      admin_password: ''
    });
    setShowUserModal(true);
  };

  const handleEditUser = (user: User) => {
    setEditingUser(user);
    setUserForm({
      email: user.email,
      role: user.role,
      status: user.status,
      first_name: user.profile?.first_name || '',
      last_name: user.profile?.last_name || '',
      admin_password: ''
    });
    setShowUserModal(true);
  };

  /*
  const handleCreateRole = () => {
    setEditingRole(null);
    setRoleForm({
      name: '',
      description: '',
      permissions: []
    });
    setShowRoleModal(true);
  };
  */

  /*
  const handleEditRole = (role: Role) => {
    setEditingRole(role);
    setRoleForm({
      name: role.name,
      description: role.description,
      permissions: role.permissions
    });
    setShowRoleModal(true);
  };
  */

  const handleSubmitUser = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const url = editingUser ? `/api/auth/users/${editingUser.id}` : '/api/auth/users';
      const method = editingUser ? 'PUT' : 'POST';
      
      const response = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders()
        },
        body: JSON.stringify({
          email: userForm.email,
          role: userForm.role,
          status: userForm.status,
          admin_password: userForm.admin_password,
          profile: {
            first_name: userForm.first_name,
            last_name: userForm.last_name,
          }
        })
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to save user');
      }

      await fetchUsersData();
      setShowUserModal(false);
      setUserForm(prev => ({ ...prev, admin_password: '' }));
      if (!editingUser && payload.setup_token) {
        setIssuedToken({
          token: payload.setup_token,
          email: payload.email,
          purpose: 'setup',
          expiresAt: payload.expires_at,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save user');
    }
  };

  const handleDeleteUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!deletingUser) return;
    try {
      const response = await fetch(`/api/auth/users/${deletingUser.id}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ admin_password: adminPassword }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || 'Failed to delete user');
      }

      await fetchUsersData();
      setDeletingUser(null);
      setAdminPassword('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete user');
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resettingUser) return;
    try {
      const response = await fetch(`/api/auth/users/${resettingUser.id}/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ admin_password: adminPassword }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to reset password');
      }
      setIssuedToken({
        token: payload.reset_token,
        email: resettingUser.email,
        purpose: 'reset',
        expiresAt: payload.expires_at,
      });
      setResettingUser(null);
      setAdminPassword('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset password');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="error" className="mb-4">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">User Management</h1>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchUsersData}
            className="flex items-center space-x-1"
          >
            <RefreshCw className="h-4 w-4" />
            <span>Refresh</span>
          </Button>
          {/*
          <Button
            variant="outline"
            size="sm"
            onClick={handleCreateRole}
            className="flex items-center space-x-1"
          >
            <Shield className="h-4 w-4" />
            <span>Manage Roles</span>
          </Button>
          */}
          <Button
            onClick={handleCreateUser}
            className="flex items-center space-x-1"
          >
            <UserPlus className="h-4 w-4" />
            <span>Add User</span>
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Users</CardTitle>
            <UsersIcon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{users.length}</div>
            <p className="text-xs text-muted-foreground">
              {users.filter(u => u.status === 'active').length} active
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Admins</CardTitle>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {users.filter(u => u.role === 'admin').length}
            </div>
            <p className="text-xs text-muted-foreground">
              System administrators
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Traders</CardTitle>
            <UsersIcon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {users.filter(u => u.role === 'trader').length}
            </div>
            <p className="text-xs text-muted-foreground">
              Trading accounts
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Viewers</CardTitle>
            <UsersIcon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {users.filter(u => u.role === 'viewer').length}
            </div>
            <p className="text-xs text-muted-foreground">
              Read-only accounts
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Search</label>
              <Input
                placeholder="Search users..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Role</label>
              <Select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value)}
              >
                <option value="all">All Roles</option>
                <option value="admin">Admin</option>
                <option value="trader">Trader</option>
                <option value="viewer">Viewer</option>
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Status</label>
              <Select
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value)}
              >
                <option value="all">All Status</option>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </Select>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between">
            <p className="text-sm text-gray-500">
              Showing {filteredUsers.length} of {users.length} users
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setSearchQuery('');
                setSelectedRole('all');
                setSelectedStatus('all');
              }}
            >
              Clear Filters
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Users Table */}
      <Card>
        <CardHeader>
          <CardTitle>Users</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    User
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Role
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Created
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Last Login
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {filteredUsers.map((user) => (
                  <tr key={user.id}>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        <div className="ml-4">
                          <div className="text-sm font-medium text-gray-900">
                            {user.profile?.first_name || user.profile?.last_name 
                              ? `${user.profile.first_name || ''} ${user.profile.last_name || ''}`.trim()
                              : user.username}
                          </div>
                          <div className="text-sm text-gray-500">{user.email}</div>
                          <div className="text-xs text-gray-400">@{user.username}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Badge variant={getRoleBadgeColor(user.role)}>
                        {user.role.toUpperCase()}
                      </Badge>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <Badge variant={getStatusBadgeColor(user.status)}>
                        {user.status.toUpperCase()}
                      </Badge>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {new Date(user.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {user.last_login ? new Date(user.last_login).toLocaleDateString() : 'Never'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      <div className="flex items-center space-x-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleEditUser(user)}
                          className="p-1"
                        >
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setResettingUser(user);
                            setAdminPassword('');
                          }}
                          className="p-1"
                        >
                          <Key className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setDeletingUser(user);
                            setAdminPassword('');
                          }}
                          className="p-1 text-red-600 hover:text-red-700"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredUsers.length === 0 && (
              <div className="text-center py-8 text-gray-500">
                No users found matching your filters
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* User Modal */}
      <Modal
        isOpen={showUserModal}
        onClose={() => {
          setShowUserModal(false);
          setUserForm(prev => ({ ...prev, admin_password: '' }));
        }}
        title={editingUser ? 'Edit User' : 'Create User'}
      >
        <form onSubmit={handleSubmitUser} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Email</label>
            <Input
              type="email"
              value={userForm.email}
              onChange={(e) => setUserForm(prev => ({ ...prev, email: e.target.value }))}
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">First Name</label>
              <Input
                value={userForm.first_name}
                onChange={(e) => setUserForm(prev => ({ ...prev, first_name: e.target.value }))}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Last Name</label>
              <Input
                value={userForm.last_name}
                onChange={(e) => setUserForm(prev => ({ ...prev, last_name: e.target.value }))}
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Confirm Your Admin Password</label>
            <Input
              type="password"
              autoComplete="current-password"
              value={userForm.admin_password}
              onChange={(e) => setUserForm(prev => ({ ...prev, admin_password: e.target.value }))}
              required
            />
            <p className="mt-1 text-xs text-gray-500">
              Required again for every account-management change.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Role</label>
              <Select
                value={userForm.role}
                onChange={(e) => setUserForm(prev => ({ ...prev, role: e.target.value as User['role'] }))}
              >
                <option value="admin">Admin</option>
                <option value="trader">Trader</option>
                <option value="viewer">Viewer</option>
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Status</label>
              <Select
                value={userForm.status}
                onChange={(e) => setUserForm(prev => ({ ...prev, status: e.target.value as User['status'] }))}
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </Select>
            </div>
          </div>

          <div className="flex justify-end space-x-2 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setShowUserModal(false);
                setUserForm(prev => ({ ...prev, admin_password: '' }));
              }}
            >
              Cancel
            </Button>
            <Button type="submit">
              {editingUser ? 'Update User' : 'Create User'}
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        isOpen={resettingUser !== null}
        onClose={() => {
          setResettingUser(null);
          setAdminPassword('');
        }}
        title="Issue Password Reset Token"
        size="sm"
      >
        <form onSubmit={handleResetPassword} className="space-y-4">
          <p className="text-sm text-gray-600">
            Confirm your administrator password. The user will choose their own password
            with a short-lived, single-use token.
          </p>
          <Input
            type="password"
            autoComplete="current-password"
            value={adminPassword}
            onChange={(event) => setAdminPassword(event.target.value)}
            required
          />
          <div className="flex justify-end space-x-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setResettingUser(null);
                setAdminPassword('');
              }}
            >
              Cancel
            </Button>
            <Button type="submit">Issue Reset Token</Button>
          </div>
        </form>
      </Modal>

      <Modal
        isOpen={deletingUser !== null}
        onClose={() => {
          setDeletingUser(null);
          setAdminPassword('');
        }}
        title="Delete User"
        size="sm"
      >
        <form onSubmit={handleDeleteUser} className="space-y-4">
          <p className="text-sm text-gray-600">
            Permanently delete {deletingUser?.email}? Confirm your administrator password.
          </p>
          <Input
            type="password"
            autoComplete="current-password"
            value={adminPassword}
            onChange={(event) => setAdminPassword(event.target.value)}
            required
          />
          <div className="flex justify-end space-x-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setDeletingUser(null);
                setAdminPassword('');
              }}
            >
              Cancel
            </Button>
            <Button type="submit" variant="danger">Delete User</Button>
          </div>
        </form>
      </Modal>

      <Modal
        isOpen={issuedToken !== null}
        onClose={() => setIssuedToken(null)}
        title={issuedToken?.purpose === 'setup' ? 'Account Setup Token' : 'Password Reset Token'}
        size="sm"
      >
        <div className="space-y-4">
          <Alert variant="warning">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Shown only once</AlertTitle>
            <AlertDescription>
              Send this token to {issuedToken?.email} through a secure channel. It expires at{' '}
              {issuedToken ? new Date(issuedToken.expiresAt).toLocaleTimeString() : ''}.
            </AlertDescription>
          </Alert>
          <Input readOnly value={issuedToken?.token ?? ''} aria-label="One-time token" />
          <p className="text-xs text-gray-500">
            The user must open /reset-password and paste this token. It is never stored in plaintext.
          </p>
          <div className="flex justify-end space-x-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => issuedToken && navigator.clipboard.writeText(issuedToken.token)}
            >
              Copy Token
            </Button>
            <Button type="button" onClick={() => setIssuedToken(null)}>Done</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
