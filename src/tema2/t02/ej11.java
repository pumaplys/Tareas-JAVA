package tema2.t02;

import java.util.Scanner;

// Programa un juego que genere un número aleatorio y te permita introducir números hasta que des con el adecuado.
// El juego deberá indicarte si el número introducido es mayor o menor.
public class ej11 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        int numeroSecreto = (int)(Math.random() * 100) + 1;

        int numUsuario;
        int intento = 0;

        System.out.println("¡Adivina el número entre 1 y 100!");

        do {
            System.out.print("Introduce tu número: ");
            numUsuario = sc.nextInt();
            intento++;

            if (numUsuario > numeroSecreto) {
                System.out.println("El numero es mayor.");
            } else if (numUsuario < numeroSecreto) {
                System.out.println("El numero es menor.");
            } else {
                System.out.println("¡Felicidades! Has acertado el numero en " + intento + " intentos.");
            }

        } while (numUsuario != numeroSecreto);
    }
}
