import { useAuth } from '../lib/auth.jsx';
import { useServer } from '../lib/server.jsx';
import SignIn from '../components/SignIn.jsx';

export default function Account() {
  const { account, signOut } = useAuth();
  const { loginRequired } = useServer();
  return (
    <div className="admin">
      <h1>Account</h1>
      {account ? (
        <>
          <p>
            Signed in{account.email ? <> as <b>{account.email}</b></> : null}. Your reports and votes are linked to an account id so one person cannot corroborate their own report. The id is never shown publicly.
          </p>
          <div className="row">
            <button className="ghost" onClick={signOut}>Sign out</button>
          </div>
        </>
      ) : (
        <SignIn reason={loginRequired ? 'This server asks you to sign in before you report, confirm or dispute an issue. Reading and comparing routes needs no account.' : 'An account is optional here. It lets you report and vote with your own pseudonymous id.'} />
      )}
    </div>
  );
}
